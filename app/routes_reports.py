"""
Reports Routes
"""
from fastapi import APIRouter, Request, Query, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import os
import sys
import csv
import io
from datetime import datetime, timedelta

from .auth import get_current_user, require_auth, can_access_campus
from .models import get_session

# Add path for mirror database
sys.path.insert(0, '/opt/portal_app/aosParishStaq/src')

router = APIRouter(prefix="/reports", tags=["Reports"])

templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)


@router.get("/", response_class=HTMLResponse)
# Reports home needs mirror db
@require_auth
async def reports_home(request: Request):
    """Reports home page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    from mirror_database import get_mirror_db, Campus
    mirror = get_mirror_db()
    try:
        if user['is_admin']:
            campuses = mirror.session.query(Campus).filter(Campus.active == True).order_by(Campus.name).all()
        else:
            campus_ids = [c['id'] for c in user.get('campuses', [])]
            campuses = mirror.session.query(Campus).filter(Campus.campus_id.in_(campus_ids)).order_by(Campus.name).all()
        
        return templates.TemplateResponse("reports/index.html", {
            "request": request,
            "user": user,
            "campuses": campuses
        })
    finally:
        pass  # mirror session managed by get_mirror_db


@router.get("/demographics", response_class=HTMLResponse)
@require_auth
async def demographics_report(
    request: Request,
    campus_id: int = Query(None)
):
    """Demographics report"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    if campus_id and not can_access_campus(request, campus_id):
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Access Denied"
        }, status_code=403)
    
    try:
        from mirror_database import Individual, Campus, get_mirror_db
        from sqlalchemy import func, case
        
        mirror = get_mirror_db()
        
        # Base query
        query = mirror.session.query(Individual)
        if campus_id:
            query = query.filter(Individual.campus_id == campus_id)
            campus = mirror.session.query(Campus).filter(Campus.campus_id == campus_id).first()
        else:
            campus = None
        
        # Get stats
        total = query.count()
        active = query.filter(Individual.active == True).count()
        
        # Membership tenure distribution
        today = datetime.now().date()
        tenure_groups = {
            '0-1 years': 0,
            '2-5 years': 0,
            '6-10 years': 0,
            '11-20 years': 0,
            '20+ years': 0,
            'Unknown': 0
        }
        
        for ind in query.all():
            if ind.membership_date:
                age = (today - ind.membership_date).days // 365
                if age <= 1:
                    tenure_groups['0-1 years'] += 1
                elif age <= 5:
                    tenure_groups['2-5 years'] += 1
                elif age <= 10:
                    tenure_groups['6-10 years'] += 1
                elif age <= 20:
                    tenure_groups['11-20 years'] += 1
                else:
                    tenure_groups['20+ years'] += 1
            else:
                tenure_groups['Unknown'] += 1
        
        # Get campuses for filter
        db = get_session()
        if user['is_admin']:
            campuses = mirror.session.query(Campus).filter(Campus.active == True).order_by(Campus.name).all()
        else:
            campus_ids_list = [c['id'] for c in user.get('campuses', [])]
            campuses = mirror.session.query(Campus).filter(Campus.campus_id.in_(campus_ids_list)).order_by(Campus.name).all()
        pass  # mirror session managed by get_mirror_db
        
        return templates.TemplateResponse("reports/demographics.html", {
            "request": request,
            "user": user,
            "campus": campus,
            "campus_id": campus_id,
            "campuses": campuses,
            "total": total,
            "active": active,
            "inactive": total - active,
            "tenure_groups": tenure_groups
        })
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Report Error",
            "message": str(e)
        })


@router.get("/membership", response_class=HTMLResponse)
@require_auth
async def membership_report(
    request: Request,
    campus_id: int = Query(None)
):
    """Membership trends report"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    try:
        from mirror_database import Individual, Campus, get_mirror_db
        from sqlalchemy import func
        
        mirror = get_mirror_db()
        
        # Get campus info
        campus = None
        if campus_id:
            campus = mirror.session.query(Campus).filter(Campus.campus_id == campus_id).first()
        
        # Monthly new members (last 12 months)
        monthly_data = []
        for i in range(11, -1, -1):
            month_start = datetime.now().replace(day=1) - timedelta(days=i*30)
            month_end = month_start + timedelta(days=30)
            
            query = mirror.session.query(Individual).filter(
                Individual.mirror_created_at >= month_start,
                Individual.mirror_created_at < month_end
            )
            if campus_id:
                query = query.filter(Individual.campus_id == campus_id)
            
            monthly_data.append({
                'month': month_start.strftime('%b %Y'),
                'count': query.count()
            })
        
        # Get campuses for filter
        db = get_session()
        if user['is_admin']:
            campuses = mirror.session.query(Campus).filter(Campus.active == True).order_by(Campus.name).all()
        else:
            campus_ids_list = [c['id'] for c in user.get('campuses', [])]
            campuses = mirror.session.query(Campus).filter(Campus.campus_id.in_(campus_ids_list)).order_by(Campus.name).all()
        pass  # mirror session managed by get_mirror_db
        
        return templates.TemplateResponse("reports/membership.html", {
            "request": request,
            "user": user,
            "campus": campus,
            "campus_id": campus_id,
            "campuses": campuses,
            "monthly_data": monthly_data
        })
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Report Error",
            "message": str(e)
        })


@router.get("/export/individuals")
@require_auth
async def export_individuals(
    request: Request,
    campus_id: int = Query(...)
):
    """Export individuals as CSV"""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    
    if not can_access_campus(request, campus_id):
        return JSONResponse({"error": "Access denied"}, status_code=403)
    
    try:
        from mirror_database import Individual, Campus, get_mirror_db
        
        mirror = get_mirror_db()
        campus = mirror.session.query(Campus).filter(Campus.campus_id == campus_id).first()
        individuals = mirror.session.query(Individual).filter(Individual.campus_id == campus_id).all()
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'Individual ID', 'First Name', 'Last Name', 'Email', 'Phone',
            'Birth Date', 'Gender', 'Active', 'Created At'
        ])
        
        for ind in individuals:
            writer.writerow([
                ind.individual_id,
                ind.first_name,
                ind.last_name,
                ind.email,
                ind.phone,
                ind.membership_date.isoformat() if ind.membership_date else ind.membership_date.isoformat() if ind.membership_date else '',
                ind.gender,
                'Yes' if ind.active else 'No',
                ind.mirror_created_at.isoformat() if ind.mirror_created_at else ''
            ])
        
        output.seek(0)
        filename = f"{campus.name.replace(' ', '_')}_individuals_{datetime.now().strftime('%Y%m%d')}.csv"
        
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
