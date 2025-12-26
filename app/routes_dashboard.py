"""
Dashboard and Report Routes
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional

from .models import User, get_db
from .auth import get_current_user

router = APIRouter(tags=["Dashboard"])
templates = Jinja2Templates(directory="templates")


# Try to import mirror database for reports
try:
    from mirror_database import get_mirror_db, Individual, Family, Campus
    MIRROR_AVAILABLE = True
except ImportError:
    MIRROR_AVAILABLE = False


# =============================================================================
# Dashboard
# =============================================================================

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page - redirect to login or dashboard"""
    token = request.cookies.get("access_token")
    if token:
        return templates.TemplateResponse("redirect.html", {
            "request": request,
            "url": "/dashboard"
        })
    return templates.TemplateResponse("redirect.html", {
        "request": request,
        "url": "/auth/login"
    })


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Main dashboard"""
    stats = {}
    
    if MIRROR_AVAILABLE:
        mirror_db = get_mirror_db()
        stats = {
            'total_individuals': mirror_db.session.query(Individual).count(),
            'active_individuals': mirror_db.session.query(Individual).filter(Individual.active == True).count(),
            'total_families': mirror_db.session.query(Family).count(),
            'total_campuses': mirror_db.session.query(Campus).count(),
        }
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": current_user,
        "stats": stats,
        "mirror_available": MIRROR_AVAILABLE
    })


# =============================================================================
# Campus Reports
# =============================================================================

@router.get("/campuses", response_class=HTMLResponse)
async def campus_list(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    """List all campuses"""
    if not MIRROR_AVAILABLE:
        raise HTTPException(status_code=503, detail="Mirror database not available")
    
    mirror_db = get_mirror_db()
    campuses = mirror_db.session.query(Campus).order_by(Campus.name).all()
    
    # Filter by user's allowed campuses
    if current_user.allowed_campuses != 'all':
        allowed = [int(c.strip()) for c in current_user.allowed_campuses.split(',') if c.strip().isdigit()]
        campuses = [c for c in campuses if c.campus_id in allowed]
    
    # Get counts for each campus
    campus_data = []
    for campus in campuses:
        individual_count = mirror_db.session.query(Individual).filter(
            Individual.campus_id == campus.campus_id,
            Individual.active == True
        ).count()
        family_count = mirror_db.session.query(Family).filter(
            Family.campus_id == campus.campus_id
        ).count()
        
        campus_data.append({
            'campus': campus,
            'individuals': individual_count,
            'families': family_count
        })
    
    return templates.TemplateResponse("campuses.html", {
        "request": request,
        "user": current_user,
        "campus_data": campus_data
    })


@router.get("/campus/{campus_id}", response_class=HTMLResponse)
async def campus_detail(
    request: Request,
    campus_id: int,
    current_user: User = Depends(get_current_user)
):
    """Campus detail page with families"""
    if not MIRROR_AVAILABLE:
        raise HTTPException(status_code=503, detail="Mirror database not available")
    
    # Check access
    if not current_user.can_access_campus(campus_id):
        raise HTTPException(status_code=403, detail="Access denied to this campus")
    
    mirror_db = get_mirror_db()
    
    campus = mirror_db.session.query(Campus).filter_by(campus_id=campus_id).first()
    if not campus:
        raise HTTPException(status_code=404, detail="Campus not found")
    
    # Get families for this campus
    families = mirror_db.session.query(Family).filter(
        Family.campus_id == campus_id
    ).order_by(Family.head_last_name, Family.head_first_name).limit(100).all()
    
    # Get stats
    stats = {
        'total_families': mirror_db.session.query(Family).filter(Family.campus_id == campus_id).count(),
        'total_individuals': mirror_db.session.query(Individual).filter(
            Individual.campus_id == campus_id, Individual.active == True
        ).count(),
        'heads': mirror_db.session.query(Individual).filter(
            Individual.campus_id == campus_id,
            Individual.household_position == 'PRIMARY_CONTACT',
            Individual.active == True
        ).count(),
    }
    
    return templates.TemplateResponse("campus_detail.html", {
        "request": request,
        "user": current_user,
        "campus": campus,
        "families": families,
        "stats": stats
    })


# =============================================================================
# Family/Individual Search
# =============================================================================

@router.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """Search individuals and families"""
    results = []
    
    if q and MIRROR_AVAILABLE:
        mirror_db = get_mirror_db()
        
        # Search by name
        search_term = f"%{q}%"
        results = mirror_db.session.query(Individual).filter(
            Individual.active == True,
            (Individual.first_name.ilike(search_term)) | 
            (Individual.last_name.ilike(search_term)) |
            (Individual.email.ilike(search_term))
        ).limit(50).all()
        
        # Filter by allowed campuses
        if current_user.allowed_campuses != 'all':
            allowed = [int(c.strip()) for c in current_user.allowed_campuses.split(',') if c.strip().isdigit()]
            results = [r for r in results if r.campus_id in allowed]
    
    return templates.TemplateResponse("search.html", {
        "request": request,
        "user": current_user,
        "query": q,
        "results": results,
        "mirror_available": MIRROR_AVAILABLE
    })
