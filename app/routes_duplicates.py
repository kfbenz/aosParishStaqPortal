"""
Duplicate Detector Routes - Frontend for parishstaq_duplicate_manager
"""
import os
import sys
import subprocess
import threading
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any

from .models import User, get_db
from .auth import get_current_user, get_current_active_admin

router = APIRouter(prefix="/duplicates", tags=["Duplicates"])
templates = Jinja2Templates(directory="templates")

# Try to import local duplicate scanner
try:
    sys.path.insert(0, '/opt/portal_app/aosParishStaq/src')
    from parishstaq_duplicate_manager import LocalDuplicateScanner, Config as DupConfig
    SCANNER_AVAILABLE = True
except ImportError as e:
    SCANNER_AVAILABLE = False
    print(f"Duplicate scanner not available: {e}")

# Store scan results in memory
_scan_results = {}
_running_scans = {}


def get_scanner():
    """Get a configured duplicate scanner"""
    if not SCANNER_AVAILABLE:
        return None
    try:
        config = DupConfig()
        scanner = LocalDuplicateScanner(config)
        if scanner.is_available():
            return scanner
    except Exception as e:
        print(f"Error creating scanner: {e}")
    return None


def run_scan_job(job_id: str, scan_type: str, active_only: bool = True):
    """Run duplicate scan in background"""
    global _scan_results, _running_scans
    
    _running_scans[job_id] = {
        'status': 'running',
        'scan_type': scan_type,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'progress': 0
    }
    
    try:
        scanner = get_scanner()
        if not scanner:
            _running_scans[job_id]['status'] = 'error'
            _running_scans[job_id]['error'] = 'Scanner not available'
            return
        
        def progress_callback(processed, total, found):
            if total > 0:
                _running_scans[job_id]['progress'] = int((processed / total) * 100)
                _running_scans[job_id]['found'] = found
        
        results = []
        
        if scan_type == 'individuals_email':
            results = scanner.find_individual_duplicates(
                active_only=active_only,
                progress_callback=progress_callback
            )
            # Filter to high confidence only
            results = [r for r in results if r.get('confidence') == 'high']
            
        elif scan_type == 'individuals_address':
            results = scanner.find_individual_duplicates_by_address(
                active_only=active_only,
                progress_callback=progress_callback
            )
            results = [r for r in results if r.get('confidence') == 'high']
            
        elif scan_type == 'individuals_phone':
            results = scanner.find_individual_duplicates_by_phone(
                active_only=active_only,
                progress_callback=progress_callback
            )
            
        elif scan_type == 'families':
            results = scanner.find_family_duplicates(
                progress_callback=progress_callback
            )
        
        _scan_results[job_id] = {
            'scan_type': scan_type,
            'results': results,
            'total_clusters': len(results),
            'total_records': sum(r.get('cluster_size', 0) for r in results),
            'high_confidence': sum(1 for r in results if r.get('confidence') == 'high'),
            'completed_at': datetime.now(timezone.utc).isoformat()
        }
        
        _running_scans[job_id]['status'] = 'completed'
        _running_scans[job_id]['completed_at'] = datetime.now(timezone.utc).isoformat()
        
    except Exception as e:
        _running_scans[job_id]['status'] = 'error'
        _running_scans[job_id]['error'] = str(e)


# =============================================================================
# Routes
# =============================================================================

@router.get("", response_class=HTMLResponse)
async def duplicates_dashboard(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    """Duplicate detection dashboard"""
    scanner = get_scanner()
    
    stats = {}
    if scanner:
        stats = {
            'total_individuals': scanner.get_record_count(),
            'active_individuals': scanner.get_active_count(),
            'total_families': scanner.get_family_count()
        }
    
    # Get running scans
    running = [
        {'id': k, **v} for k, v in _running_scans.items()
        if v['status'] == 'running'
    ]
    
    # Get recent completed scans
    completed = []
    for job_id, scan in _running_scans.items():
        if scan['status'] != 'running' and job_id in _scan_results:
            completed.append({
                'id': job_id,
                **scan,
                'results_summary': {
                    'total_clusters': _scan_results[job_id]['total_clusters'],
                    'total_records': _scan_results[job_id]['total_records']
                }
            })
    completed = sorted(completed, key=lambda x: x.get('completed_at', ''), reverse=True)[:5]
    
    return templates.TemplateResponse("duplicates/dashboard.html", {
        "request": request,
        "user": current_user,
        "scanner_available": SCANNER_AVAILABLE and scanner is not None,
        "stats": stats,
        "running_scans": running,
        "completed_scans": completed
    })


@router.post("/scan")
async def start_scan(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    """Start a duplicate scan"""
    form = await request.form()
    scan_type = form.get("scan_type", "individuals_email")
    active_only = form.get("active_only", "on") == "on"
    
    job_id = f"{scan_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    thread = threading.Thread(
        target=run_scan_job,
        args=(job_id, scan_type, active_only)
    )
    thread.start()
    
    return {"job_id": job_id, "status": "started"}


@router.get("/scan/{job_id}/status")
async def get_scan_status(
    job_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get scan status"""
    if job_id not in _running_scans:
        raise HTTPException(status_code=404, detail="Scan not found")
    
    return _running_scans[job_id]


@router.get("/scan/{job_id}/results", response_class=HTMLResponse)
async def get_scan_results(
    request: Request,
    job_id: str,
    page: int = 1,
    current_user: User = Depends(get_current_user)
):
    """View scan results"""
    if job_id not in _scan_results:
        raise HTTPException(status_code=404, detail="Results not found")
    
    results_data = _scan_results[job_id]
    results = results_data['results']
    
    # Pagination
    per_page = 20
    total_pages = (len(results) + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_results = results[start:end]
    
    scan_type_labels = {
        'individuals_email': 'Individuals (Name + Email)',
        'individuals_address': 'Individuals (Name + Address)',
        'individuals_phone': 'Individuals (No Address/Email - Phone)',
        'families': 'Families'
    }
    
    return templates.TemplateResponse("duplicates/results.html", {
        "request": request,
        "user": current_user,
        "job_id": job_id,
        "scan_type": results_data['scan_type'],
        "scan_type_label": scan_type_labels.get(results_data['scan_type'], results_data['scan_type']),
        "results": paginated_results,
        "total_clusters": results_data['total_clusters'],
        "total_records": results_data['total_records'],
        "high_confidence": results_data['high_confidence'],
        "page": page,
        "total_pages": total_pages,
        "completed_at": results_data['completed_at']
    })


@router.get("/cluster/{job_id}/{cluster_index}", response_class=HTMLResponse)
async def view_cluster(
    request: Request,
    job_id: str,
    cluster_index: int,
    current_user: User = Depends(get_current_user)
):
    """View details of a specific duplicate cluster"""
    if job_id not in _scan_results:
        raise HTTPException(status_code=404, detail="Results not found")
    
    results = _scan_results[job_id]['results']
    
    if cluster_index < 0 or cluster_index >= len(results):
        raise HTTPException(status_code=404, detail="Cluster not found")
    
    cluster = results[cluster_index]
    
    return templates.TemplateResponse("duplicates/cluster_detail.html", {
        "request": request,
        "user": current_user,
        "job_id": job_id,
        "cluster_index": cluster_index,
        "cluster": cluster,
        "scan_type": _scan_results[job_id]['scan_type']
    })
