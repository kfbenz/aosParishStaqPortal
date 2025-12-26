"""
ParishStaq Mirror Routes - Sync Management Frontend
"""
import os
import sys
import subprocess
import threading
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional
import json

from .models import User, get_db
from .auth import get_current_user, get_current_active_admin

router = APIRouter(prefix="/mirror", tags=["Mirror"])
templates = Jinja2Templates(directory="templates")

# Path to mirror script
MIRROR_SCRIPT = "/opt/portal_app/aosParishStaq/src/parishstaq_mirror.py"
MIRROR_VENV_PYTHON = "/opt/portal_app/aosParishStaq/venv/bin/python3"

# Store running jobs
_running_jobs = {}


# Try to import mirror database for status
try:
    from mirror_database import get_mirror_db, Individual, Family, Campus, MirrorStatus
    MIRROR_AVAILABLE = True
except ImportError:
    MIRROR_AVAILABLE = False


def get_mirror_stats():
    """Get current mirror database statistics"""
    if not MIRROR_AVAILABLE:
        return None
    
    try:
        db = get_mirror_db()
        
        # Get last sync status
        last_sync = db.session.query(MirrorStatus).order_by(
            MirrorStatus.started_at.desc()
        ).first()
        
        stats = {
            'total_individuals': db.session.query(Individual).count(),
            'active_individuals': db.session.query(Individual).filter(Individual.active == True).count(),
            'inactive_individuals': db.session.query(Individual).filter(Individual.active == False).count(),
            'total_families': db.session.query(Family).count(),
            'total_campuses': db.session.query(Campus).count(),
            'last_sync': {
                'started_at': last_sync.started_at.isoformat() if last_sync and last_sync.started_at else None,
                'completed_at': last_sync.completed_at.isoformat() if last_sync and last_sync.completed_at else None,
                'status': last_sync.status if last_sync else None,
                'individuals_processed': last_sync.individuals_processed if last_sync else 0,
                'refresh_type': last_sync.refresh_type if last_sync else None,
            } if last_sync else None
        }
        
        # Get deceased count
        stats['deceased_individuals'] = db.session.query(Individual).filter(
            Individual.deceased_date.isnot(None)
        ).count()
        
        # Get geocoded count
        stats['geocoded_individuals'] = db.session.query(Individual).filter(
            Individual.latitude.isnot(None),
            Individual.longitude.isnot(None)
        ).count()
        
        return stats
    except Exception as e:
        return {'error': str(e)}


def run_mirror_command(job_id: str, command: list, description: str):
    """Run mirror command in background"""
    global _running_jobs
    
    _running_jobs[job_id] = {
        'status': 'running',
        'description': description,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'output': [],
        'return_code': None
    }
    
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=os.path.dirname(MIRROR_SCRIPT)
        )
        
        for line in process.stdout:
            _running_jobs[job_id]['output'].append(line.rstrip())
            # Keep only last 500 lines
            if len(_running_jobs[job_id]['output']) > 500:
                _running_jobs[job_id]['output'] = _running_jobs[job_id]['output'][-500:]
        
        process.wait()
        _running_jobs[job_id]['return_code'] = process.returncode
        _running_jobs[job_id]['status'] = 'completed' if process.returncode == 0 else 'failed'
        
    except Exception as e:
        _running_jobs[job_id]['status'] = 'error'
        _running_jobs[job_id]['output'].append(f"Error: {str(e)}")
    
    _running_jobs[job_id]['completed_at'] = datetime.now(timezone.utc).isoformat()


# =============================================================================
# Routes
# =============================================================================

@router.get("", response_class=HTMLResponse)
async def mirror_dashboard(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    """Mirror management dashboard"""
    stats = get_mirror_stats()
    
    # Get running jobs
    running = [
        {'id': k, **v} for k, v in _running_jobs.items() 
        if v['status'] == 'running'
    ]
    
    # Get recent completed jobs
    completed = sorted(
        [{'id': k, **v} for k, v in _running_jobs.items() if v['status'] != 'running'],
        key=lambda x: x.get('completed_at', ''),
        reverse=True
    )[:5]
    
    return templates.TemplateResponse("mirror/dashboard.html", {
        "request": request,
        "user": current_user,
        "stats": stats,
        "mirror_available": MIRROR_AVAILABLE,
        "running_jobs": running,
        "completed_jobs": completed
    })


@router.post("/refresh/incremental")
async def start_incremental_refresh(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_admin)
):
    """Start incremental refresh (last 7 days)"""
    form = await request.form()
    days = int(form.get("days", 7))
    
    job_id = f"incremental_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    command = [MIRROR_VENV_PYTHON, MIRROR_SCRIPT, "--refresh", str(days)]
    
    thread = threading.Thread(
        target=run_mirror_command,
        args=(job_id, command, f"Incremental refresh ({days} days)")
    )
    thread.start()
    
    return {"job_id": job_id, "status": "started"}


@router.post("/refresh/full")
async def start_full_refresh(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_admin)
):
    """Start full refresh"""
    job_id = f"full_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    command = [MIRROR_VENV_PYTHON, MIRROR_SCRIPT, "--full-refresh"]
    
    thread = threading.Thread(
        target=run_mirror_command,
        args=(job_id, command, "Full refresh")
    )
    thread.start()
    
    return {"job_id": job_id, "status": "started"}


@router.get("/job/{job_id}")
async def get_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get job status and output"""
    if job_id not in _running_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = _running_jobs[job_id]
    return {
        "job_id": job_id,
        "status": job['status'],
        "description": job['description'],
        "started_at": job['started_at'],
        "completed_at": job.get('completed_at'),
        "return_code": job.get('return_code'),
        "output": job['output'][-100:]  # Last 100 lines
    }


@router.get("/job/{job_id}/output")
async def get_job_output(
    job_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get full job output"""
    if job_id not in _running_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {"output": _running_jobs[job_id]['output']}


@router.get("/sync-history", response_class=HTMLResponse)
async def sync_history(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    """View sync history"""
    history = []
    
    if MIRROR_AVAILABLE:
        try:
            db = get_mirror_db()
            syncs = db.session.query(MirrorStatus).order_by(
                MirrorStatus.started_at.desc()
            ).limit(50).all()
            
            history = [{
                'id': s.id,
                'refresh_type': s.refresh_type,
                'status': s.status,
                'started_at': s.started_at,
                'completed_at': s.completed_at,
                'individuals_processed': s.individuals_processed,
                'last_page': s.last_page,
                'error_message': s.error_message
            } for s in syncs]
        except Exception as e:
            pass
    
    return templates.TemplateResponse("mirror/history.html", {
        "request": request,
        "user": current_user,
        "history": history,
        "mirror_available": MIRROR_AVAILABLE
    })


@router.get("/campuses-summary", response_class=HTMLResponse)
async def campuses_summary(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    """View campus summary with counts"""
    campuses_data = []
    
    if MIRROR_AVAILABLE:
        try:
            db = get_mirror_db()
            campuses = db.session.query(Campus).order_by(Campus.name).all()
            
            for campus in campuses:
                active = db.session.query(Individual).filter(
                    Individual.campus_id == campus.campus_id,
                    Individual.active == True
                ).count()
                
                families = db.session.query(Family).filter(
                    Family.campus_id == campus.campus_id
                ).count()
                
                geocoded = db.session.query(Individual).filter(
                    Individual.campus_id == campus.campus_id,
                    Individual.latitude.isnot(None)
                ).count()
                
                campuses_data.append({
                    'campus': campus,
                    'active_individuals': active,
                    'families': families,
                    'geocoded': geocoded
                })
        except Exception as e:
            pass
    
    return templates.TemplateResponse("mirror/campuses_summary.html", {
        "request": request,
        "user": current_user,
        "campuses_data": campuses_data,
        "mirror_available": MIRROR_AVAILABLE
    })
