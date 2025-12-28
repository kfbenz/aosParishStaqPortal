"""
Duplicate Detection Routes
"""
from fastapi import APIRouter, Request, Form, Query, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os
import sys
import json
import csv
import io
from datetime import datetime

from .auth import get_current_user, require_auth, require_admin
from .models import ScanJob, get_session, SessionLocal

# Add path for mirror database
sys.path.insert(0, '/opt/portal_app/aosParishStaq/src')
from mirror_database import Campus, MirrorDatabase

router = APIRouter(prefix="/duplicates", tags=["Duplicates"])

templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)


@router.get("/", response_class=HTMLResponse)
@require_auth
async def duplicates_home(request: Request):
    """Duplicate detection home page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    portal_db = SessionLocal()
    try:
        # Get recent scans
        scans = portal_db.query(ScanJob).order_by(ScanJob.created_at.desc()).limit(10).all()
        
        # Get campuses for dropdown from mirror database
        mirror_db = MirrorDatabase()
        if user['is_admin']:
            campuses = mirror_db.session.query(Campus).filter(Campus.active == True).order_by(Campus.name).all()
        else:
            campus_ids = [c['id'] for c in user.get('campuses', [])]
            campuses = mirror_db.session.query(Campus).filter(Campus.id.in_(campus_ids)).order_by(Campus.name).all()
        
        return templates.TemplateResponse("duplicates/index.html", {
            "request": request,
            "user": user,
            "scans": scans,
            "campuses": campuses
        })
    finally:
        portal_db.close()


@router.post("/scan")
@require_auth
async def start_scan(
    request: Request,
    campus_id: str = Form(""),
    scan_type: str = Form("quick")
):
    """Start a new duplicate scan"""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    
    # Convert campus_id to int
    if not campus_id or campus_id == "":
        return JSONResponse({"error": "Please select a campus"}, status_code=400)
    try:
        campus_id_int = int(campus_id)
    except ValueError:
        return JSONResponse({"error": "Invalid campus ID"}, status_code=400)
    
    portal_db = SessionLocal()
    mirror_db = MirrorDatabase()
    try:
        # Get campus info from mirror database
        campus = mirror_db.session.query(Campus).filter(Campus.campus_id == campus_id_int).first()
        if not campus:
            return JSONResponse({"error": "Campus not found"}, status_code=404)
        
        # Create scan job
        scan = ScanJob(
            campus_id=campus_id_int,
            campus_name=campus.name,
            scan_type=scan_type,
            status='running',
            started_at=datetime.utcnow(),
            created_by=user['id']
        )
        portal_db.add(scan)
        portal_db.commit()
        scan_id = scan.id
        
        # Run scan (simplified - in production this would be async)
        try:
            from parishstaq_duplicate_manager import DuplicateManager
            from mirror_database import get_mirror_db
            
            mirror = get_mirror_db()
            manager = DuplicateManager(mirror.session)
            
            # Run detection
            if scan_type == 'quick':
                clusters = manager.find_exact_duplicates(campus_id=campus_id)
            else:
                clusters = manager.find_fuzzy_duplicates(campus_id=campus_id)
            
            # Update scan job
            scan.status = 'completed'
            scan.completed_at = datetime.utcnow()
            scan.duplicates_found = len(clusters)
            scan.results_summary = json.dumps({
                'clusters': [
                    {
                        'match_type': c.get('match_type', 'unknown'),
                        'members': c.get('members', [])
                    }
                    for c in clusters[:100]  # Limit stored results
                ]
            })
            portal_db.commit()
            
            return JSONResponse({
                "success": True,
                "scan_id": scan_id,
                "duplicates_found": len(clusters)
            })
            
        except Exception as e:
            scan.status = 'failed'
            scan.error_message = str(e)[:500]
            scan.completed_at = datetime.utcnow()
            portal_db.commit()
            
            return JSONResponse({
                "success": False,
                "error": str(e)
            }, status_code=500)
            
    finally:
        portal_db.close()


@router.get("/scan/{scan_id}", response_class=HTMLResponse)
@require_auth
async def view_scan(request: Request, scan_id: int):
    """View scan results"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    portal_db = SessionLocal()
    try:
        scan = portal_db.query(ScanJob).filter(ScanJob.id == scan_id).first()
        if not scan:
            return templates.TemplateResponse("error.html", {
                "request": request,
                "error": "Scan not found"
            }, status_code=404)
        
        clusters = []
        if scan.results_summary:
            try:
                results = json.loads(scan.results_summary)
                clusters = results.get('clusters', [])
            except:
                pass
        
        return templates.TemplateResponse("duplicates/results.html", {
            "request": request,
            "user": user,
            "scan": scan,
            "clusters": clusters
        })
    finally:
        portal_db.close()


@router.get("/scan/{scan_id}/export")
@require_auth
async def export_results(request: Request, scan_id: int):
    """Export scan results as CSV"""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    
    portal_db = SessionLocal()
    try:
        scan = portal_db.query(ScanJob).filter(ScanJob.id == scan_id).first()
        if not scan or not scan.results_summary:
            return JSONResponse({"error": "No results"}, status_code=404)
        
        results = json.loads(scan.results_summary)
        clusters = results.get('clusters', [])
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Cluster', 'Individual ID', 'Name', 'Email', 'Campus', 'Match Type'])
        
        for i, cluster in enumerate(clusters, 1):
            members = cluster.get('members', cluster.get('individuals', []))
            for member in members:
                writer.writerow([
                    i,
                    member.get('individual_id', member.get('aos_id', '')),
                    f"{member.get('first_name', '')} {member.get('last_name', '')}",
                    member.get('email', ''),
                    member.get('campus_name', ''),
                    cluster.get('match_type', '')
                ])
        
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=duplicates_{scan_id}.csv"}
        )
    finally:
        portal_db.close()
