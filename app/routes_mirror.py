"""
Mirror Database Routes - Browse ParishStaq data
"""
from fastapi import APIRouter, Request, Query, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import os
import sys

from .auth import get_current_user, require_auth, can_access_campus

# Add path for mirror database
sys.path.insert(0, '/opt/portal_app/aosParishStaq/src')

router = APIRouter(prefix="/mirror", tags=["Mirror Database"])

templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)


@router.get("/", response_class=HTMLResponse)
@require_auth
async def mirror_home(request: Request):
    """Mirror database home - select campus"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    try:
        from mirror_database import Campus, get_mirror_db
        mirror = get_mirror_db()
        
        if user['is_admin']:
            campuses = mirror.session.query(Campus).order_by(Campus.name).all()
        else:
            campus_ids = [c['campus_id'] for c in user.get('campuses', [])]
            campuses = mirror.session.query(Campus).filter(Campus.campus_id.in_(campus_ids)).order_by(Campus.name).all()
        
        return templates.TemplateResponse("mirror/index.html", {
            "request": request,
            "user": user,
            "campuses": campuses
        })
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Mirror Database Error",
            "message": str(e)
        })


@router.get("/campus/{campus_id}", response_class=HTMLResponse)
@require_auth
async def campus_detail(
    request: Request,
    campus_id: int,
    page: int = Query(1, ge=1),
    search: str = Query("")
):
    """View campus individuals"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    if not can_access_campus(request, campus_id):
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Access Denied",
            "message": "You don't have access to this campus"
        }, status_code=403)
    
    try:
        from mirror_database import Campus, Individual, get_mirror_db
        from sqlalchemy import or_
        
        mirror = get_mirror_db()
        campus = mirror.session.query(Campus).filter(Campus.campus_id == campus_id).first()
        
        if not campus:
            return templates.TemplateResponse("error.html", {
                "request": request,
                "error": "Not Found",
                "message": "Campus not found"
            }, status_code=404)
        
        # Build query
        query = mirror.session.query(Individual).filter(Individual.campus_id == campus_id)
        
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Individual.first_name.ilike(search_term),
                    Individual.last_name.ilike(search_term),
                    Individual.email.ilike(search_term)
                )
            )
        
        # Pagination
        per_page = 50
        total = query.count()
        individuals = query.order_by(Individual.last_name, Individual.first_name)\
            .offset((page - 1) * per_page).limit(per_page).all()
        
        total_pages = (total + per_page - 1) // per_page
        
        return templates.TemplateResponse("mirror/campus.html", {
            "request": request,
            "user": user,
            "campus": campus,
            "individuals": individuals,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "search": search
        })
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Error",
            "message": str(e)
        })


@router.get("/individual/{individual_id}", response_class=HTMLResponse)
@require_auth
async def individual_detail(request: Request, individual_id: int):
    """View individual details"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    try:
        from mirror_database import Individual, Family, get_mirror_db
        
        mirror = get_mirror_db()
        individual = mirror.session.query(Individual).filter(Individual.aos_id == individual_id).first()
        
        if not individual:
            return templates.TemplateResponse("error.html", {
                "request": request,
                "error": "Not Found",
                "message": "Individual not found"
            }, status_code=404)
        
        if not can_access_campus(request, individual.campus_id):
            return templates.TemplateResponse("error.html", {
                "request": request,
                "error": "Access Denied",
                "message": "You don't have access to this individual's campus"
            }, status_code=403)
        
        # Get family info
        family = None
        if individual.family_id:
            family = mirror.session.query(Family).filter(Family.family_id == individual.family_id).first()
        
        return templates.TemplateResponse("mirror/individual.html", {
            "request": request,
            "user": user,
            "individual": individual,
            "family": family
        })
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Error",
            "message": str(e)
        })


@router.get("/api/search")
@require_auth
async def api_search(
    request: Request,
    q: str = Query(..., min_length=2),
    campus_id: int = Query(None)
):
    """API endpoint for searching individuals"""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    
    try:
        from mirror_database import Individual, get_mirror_db
        from sqlalchemy import or_
        
        mirror = get_mirror_db()
        query = mirror.session.query(Individual)
        
        # Filter by campus if specified
        if campus_id:
            if not can_access_campus(request, campus_id):
                return JSONResponse({"error": "Access denied"}, status_code=403)
            query = query.filter(Individual.campus_id == campus_id)
        elif not user['is_admin']:
            # Non-admins can only search their campuses
            campus_ids = [c['campus_id'] for c in user.get('campuses', [])]
            query = query.filter(Individual.campus_id.in_(campus_ids))
        
        # Search
        search_term = f"%{q}%"
        query = query.filter(
            or_(
                Individual.first_name.ilike(search_term),
                Individual.last_name.ilike(search_term),
                Individual.email.ilike(search_term)
            )
        )
        
        results = query.limit(20).all()
        
        return JSONResponse({
            "results": [
                {
                    "id": i.individual_id,
                    "name": f"{i.first_name} {i.last_name}",
                    "email": i.email,
                    "campus_id": i.campus_id
                }
                for i in results
            ]
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
