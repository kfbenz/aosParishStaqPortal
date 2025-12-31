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
from mirror_database import Campus, MirrorDatabase, Individual

router = APIRouter(prefix="/duplicates", tags=["Duplicates"])

templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)


def get_scanner():
    """Get duplicate scanner instance with config"""
    from parishstaq_duplicate_manager import LocalDuplicateScanner, Config
    config = Config()
    return LocalDuplicateScanner(config)


def get_last_modified_for_individuals(mirror_db, individual_ids: list) -> dict:
    """
    Get last_modified dates for a list of individual IDs.
    Returns dict keyed by individual_id with formatted date string.
    """
    if not individual_ids:
        return {}
    
    modified_data = {}
    
    try:
        # Convert IDs to strings for consistency
        id_list = [str(id) for id in individual_ids if id]
        if not id_list:
            return {}
        
        # Query individuals for their last_modified dates
        individuals = mirror_db.session.query(Individual).filter(
            Individual.aos_id.in_(id_list)
        ).all()
        
        for ind in individuals:
            if ind.last_modified:
                if hasattr(ind.last_modified, 'strftime'):
                    date_str = ind.last_modified.strftime('%Y-%m-%d')
                else:
                    date_str = str(ind.last_modified)[:10]
                modified_data[str(ind.aos_id)] = date_str
            
    except Exception as e:
        print(f"Error fetching last_modified data: {e}")
    
    return modified_data


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
        if user.get('is_admin'):
            campuses = mirror_db.session.query(Campus).filter(Campus.active == True).order_by(Campus.name).all()
        else:
            campus_ids = [c.get('id') for c in user.get('campuses', [])]
            if campus_ids:
                campuses = mirror_db.session.query(Campus).filter(Campus.id.in_(campus_ids)).order_by(Campus.name).all()
            else:
                campuses = []

        return templates.TemplateResponse("duplicates/index.html", {
            "request": request,
            "user": user,
            "scans": scans,
            "campuses": campuses
        })
    finally:
        portal_db.close()


@router.post("/scan")
async def start_scan(
    request: Request,
    campus_id: str = Form(""),
    scan_type: str = Form("quick")
):
    """Start a new duplicate scan"""
    # Check auth - return JSON error instead of redirect for API calls
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated", "redirect": "/auth/login"}, status_code=401)

    # Convert campus_id to int (optional - empty means all campuses)
    campus_id_int = None
    if campus_id and campus_id != "":
        try:
            campus_id_int = int(campus_id)
        except ValueError:
            return JSONResponse({"error": "Invalid campus ID"}, status_code=400)

    portal_db = SessionLocal()
    mirror_db = MirrorDatabase()
    try:
        # Get campus info if specified
        campus_name = "All Campuses"
        if campus_id_int:
            campus = mirror_db.session.query(Campus).filter(Campus.campus_id == campus_id_int).first()
            if campus:
                campus_name = campus.name

        # Create scan job
        scan = ScanJob(
            campus_id=campus_id_int,
            campus_name=campus_name,
            scan_type=scan_type,
            status='running',
            started_at=datetime.utcnow(),
            created_by=user.get('id')
        )
        portal_db.add(scan)
        portal_db.commit()
        scan_id = scan.id

        # Run scan
        try:
            scanner = get_scanner()

            # Run detection based on scan type
            if scan_type == 'address':
                clusters = scanner.find_individual_duplicates_by_address(active_only=True)
            elif scan_type == 'phone':
                clusters = scanner.find_individual_duplicates_by_phone(active_only=True)
            elif scan_type == 'family':
                clusters = scanner.find_family_duplicates()
            else:
                # Default: individual duplicates (name/email)
                clusters = scanner.find_individual_duplicates(active_only=True)

            # Filter by campus if specified
            if campus_id_int:
                filtered_clusters = []
                for c in clusters:
                    members = c.get('members', c.get('individuals', []))
                    # Keep cluster if any member is from the selected campus
                    if any(m.get('campus_id') == campus_id_int for m in members):
                        filtered_clusters.append(c)
                clusters = filtered_clusters

            # Update scan job
            scan.status = 'completed'
            scan.completed_at = datetime.utcnow()
            scan.duplicates_found = len(clusters)
            scan.results_summary = json.dumps({
                'clusters': [
                    {
                        'match_type': ', '.join(c.get('match_fields', [])) or c.get('type', 'unknown'),
                        'score': c.get('max_score', c.get('score', 0)),
                        'confidence': c.get('confidence', ''),
                        'members': c.get('members', [])[:20]
                    }
                    for c in clusters[:100]
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
    mirror_db = MirrorDatabase()
    try:
        scan = portal_db.query(ScanJob).filter(ScanJob.id == scan_id).first()
        if not scan:
            return templates.TemplateResponse("error.html", {
                "request": request,
                "error": "Scan not found"
            }, status_code=404)

        clusters = []
        last_modified = {}
        
        if scan.results_summary:
            try:
                results = json.loads(scan.results_summary)
                clusters = results.get('clusters', [])
                
                # Collect all individual IDs from clusters
                all_individual_ids = []
                for cluster in clusters:
                    members = cluster.get('members', cluster.get('individuals', []))
                    for member in members:
                        ind_id = member.get('individual_id') or member.get('aos_id')
                        if ind_id:
                            all_individual_ids.append(ind_id)
                
                # Get last_modified dates for all individuals
                if all_individual_ids:
                    last_modified = get_last_modified_for_individuals(mirror_db, all_individual_ids)
                    
            except Exception as e:
                print(f"Error loading scan results: {e}")

        return templates.TemplateResponse("duplicates/results.html", {
            "request": request,
            "user": user,
            "scan": scan,
            "clusters": clusters,
            "last_modified": last_modified
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
        writer.writerow(['Cluster', 'Individual ID', 'Name', 'Email', 'Campus', 'Match Type', 'Score'])

        for i, cluster in enumerate(clusters, 1):
            members = cluster.get('members', cluster.get('individuals', []))
            for member in members:
                writer.writerow([
                    i,
                    member.get('individual_id', member.get('aos_id', '')),
                    f"{member.get('first_name', '')} {member.get('last_name', '')}",
                    member.get('email', ''),
                    member.get('campus_name', ''),
                    cluster.get('match_type', ''),
                    cluster.get('score', '')
                ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=duplicates_{scan_id}.csv"}
        )
    finally:
        portal_db.close()
