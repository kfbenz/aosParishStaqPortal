"""
ParishStaq Portal - Geocoding Routes
Provides web interface for geocoding operations using the permanent geocoding cache.

Author: Kevin Benz - Archdiocese of Seattle IT
Date: December 2024
"""

from fastapi import APIRouter, Request, Depends, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
import os
import sys
from datetime import datetime

# Add parent paths for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, '/opt/portal_app/aosParishStaq/src')

from .auth import get_current_user, require_admin

router = APIRouter(prefix="/geocoding", tags=["Geocoding"])

templates_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_path)


# =============================================================================
# GEOCODING SERVICE (Lazy Import)
# =============================================================================

_geocoding_service = None

def get_geocoding_service():
    """Get or create the geocoding service singleton"""
    global _geocoding_service
    if _geocoding_service is None:
        from geocoding_service import GeocodingService
        _geocoding_service = GeocodingService()
    return _geocoding_service


def get_geocode_db():
    """Get geocoding database session"""
    from geocoding_database import get_geocode_session
    return get_geocode_session()


# =============================================================================
# GEOCODING DASHBOARD
# =============================================================================

@router.get("/", response_class=HTMLResponse)
async def geocoding_dashboard(request: Request, user: dict = Depends(get_current_user)):
    """Main geocoding dashboard with stats and tools"""
    try:
        from geocoding_database import GeocodeCache, get_geocode_session
        session = get_geocode_session()
        
        # Get cache statistics
        total_cached = session.query(GeocodeCache).count()
        
        # Accuracy breakdown
        from sqlalchemy import func
        accuracy_stats = session.query(
            GeocodeCache.accuracy,
            func.count(GeocodeCache.id)
        ).group_by(GeocodeCache.accuracy).all()
        
        accuracy_breakdown = {acc: count for acc, count in accuracy_stats if acc}
        
        # Provider breakdown
        provider_stats = session.query(
            GeocodeCache.geocode_provider,
            func.count(GeocodeCache.id)
        ).group_by(GeocodeCache.geocode_provider).all()
        
        provider_breakdown = {prov or 'Unknown': count for prov, count in provider_stats}
        
        # Recent geocodes
        recent = session.query(GeocodeCache).order_by(
            GeocodeCache.geocoded_at.desc()
        ).limit(10).all()
        
        # Most used addresses
        most_used = session.query(GeocodeCache).order_by(
            GeocodeCache.usage_count.desc()
        ).limit(10).all()
        
        session.close()
        
        return templates.TemplateResponse("geocoding/dashboard.html", {
            "request": request,
            "user": user,
            "total_cached": total_cached,
            "accuracy_breakdown": accuracy_breakdown,
            "provider_breakdown": provider_breakdown,
            "recent": recent,
            "most_used": most_used
        })
        
    except Exception as e:
        return templates.TemplateResponse("geocoding/dashboard.html", {
            "request": request,
            "user": user,
            "error": str(e),
            "total_cached": 0,
            "accuracy_breakdown": {},
            "provider_breakdown": {},
            "recent": [],
            "most_used": []
        })


# =============================================================================
# SINGLE ADDRESS GEOCODING
# =============================================================================

@router.get("/lookup", response_class=HTMLResponse)
async def geocode_lookup_form(request: Request, user: dict = Depends(get_current_user)):
    """Form for single address geocoding"""
    return templates.TemplateResponse("geocoding/lookup.html", {
        "request": request,
        "user": user
    })


@router.post("/lookup", response_class=HTMLResponse)
async def geocode_lookup(
    request: Request,
    street: str = Form(...),
    city: str = Form(...),
    state: str = Form("WA"),
    zip_code: str = Form(...),
    user: dict = Depends(get_current_user)
):
    """Geocode a single address"""
    try:
        service = get_geocoding_service()
        result = service.geocode_address(street, city, state, zip_code)
        
        return templates.TemplateResponse("geocoding/lookup.html", {
            "request": request,
            "user": user,
            "result": result,
            "street": street,
            "city": city,
            "state": state,
            "zip_code": zip_code
        })
        
    except Exception as e:
        return templates.TemplateResponse("geocoding/lookup.html", {
            "request": request,
            "user": user,
            "error": str(e),
            "street": street,
            "city": city,
            "state": state,
            "zip_code": zip_code
        })


# =============================================================================
# API ENDPOINTS
# =============================================================================

@router.get("/api/geocode")
async def api_geocode(
    street: str,
    city: str,
    state: str = "WA",
    zip_code: str = "",
    user: dict = Depends(get_current_user)
):
    """API endpoint for geocoding a single address"""
    try:
        service = get_geocoding_service()
        result = service.geocode_address(street, city, state, zip_code)
        
        if result:
            return JSONResponse({
                "success": True,
                "data": result
            })
        else:
            return JSONResponse({
                "success": False,
                "error": "Address could not be geocoded"
            })
            
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


@router.post("/api/geocode/batch")
async def api_batch_geocode(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """API endpoint for batch geocoding multiple addresses"""
    try:
        data = await request.json()
        addresses = data.get("addresses", [])
        
        if not addresses:
            return JSONResponse({
                "success": False,
                "error": "No addresses provided"
            }, status_code=400)
        
        if len(addresses) > 100:
            return JSONResponse({
                "success": False,
                "error": "Maximum 100 addresses per batch"
            }, status_code=400)
        
        service = get_geocoding_service()
        results = []
        
        for addr in addresses:
            result = service.geocode_address(
                addr.get("street", ""),
                addr.get("city", ""),
                addr.get("state", "WA"),
                addr.get("zip_code", "")
            )
            results.append({
                "input": addr,
                "result": result
            })
        
        return JSONResponse({
            "success": True,
            "count": len(results),
            "data": results
        })
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


# =============================================================================
# CACHE MANAGEMENT
# =============================================================================

@router.get("/cache", response_class=HTMLResponse)
async def cache_browser(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=100),
    search: str = Query(""),
    accuracy: str = Query(""),
    user: dict = Depends(get_current_user)
):
    """Browse geocoding cache entries"""
    try:
        from geocoding_database import GeocodeCache, get_geocode_session
        from sqlalchemy import or_
        
        session = get_geocode_session()
        query = session.query(GeocodeCache)
        
        # Apply filters
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    GeocodeCache.street.ilike(search_term),
                    GeocodeCache.city.ilike(search_term),
                    GeocodeCache.formatted_address.ilike(search_term),
                    GeocodeCache.zip_code.ilike(search_term)
                )
            )
        
        if accuracy:
            query = query.filter(GeocodeCache.accuracy == accuracy)
        
        # Get total count
        total = query.count()
        
        # Paginate
        offset = (page - 1) * per_page
        entries = query.order_by(GeocodeCache.geocoded_at.desc())\
            .offset(offset).limit(per_page).all()
        
        total_pages = (total + per_page - 1) // per_page
        
        session.close()
        
        return templates.TemplateResponse("geocoding/cache.html", {
            "request": request,
            "user": user,
            "entries": entries,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "search": search,
            "accuracy": accuracy
        })
        
    except Exception as e:
        return templates.TemplateResponse("geocoding/cache.html", {
            "request": request,
            "user": user,
            "error": str(e),
            "entries": [],
            "page": 1,
            "per_page": per_page,
            "total": 0,
            "total_pages": 0,
            "search": search,
            "accuracy": accuracy
        })


@router.get("/cache/{cache_id}", response_class=HTMLResponse)
async def cache_detail(
    request: Request,
    cache_id: int,
    user: dict = Depends(get_current_user)
):
    """View details of a cached geocode entry"""
    try:
        from geocoding_database import GeocodeCache, get_geocode_session
        import json
        
        session = get_geocode_session()
        entry = session.query(GeocodeCache).filter_by(id=cache_id).first()
        
        if not entry:
            session.close()
            raise HTTPException(status_code=404, detail="Cache entry not found")
        
        # Parse JSON response if available
        api_response = None
        if entry.api_response_json:
            try:
                api_response = json.loads(entry.api_response_json)
            except:
                api_response = entry.api_response_json
        
        session.close()
        
        return templates.TemplateResponse("geocoding/cache_detail.html", {
            "request": request,
            "user": user,
            "entry": entry,
            "api_response": api_response
        })
        
    except HTTPException:
        raise
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


@router.post("/cache/{cache_id}/refresh")
async def refresh_cache_entry(
    cache_id: int,
    user: dict = Depends(require_admin)
):
    """Re-geocode a cached address (admin only)"""
    try:
        from geocoding_database import GeocodeCache, get_geocode_session
        
        session = get_geocode_session()
        entry = session.query(GeocodeCache).filter_by(id=cache_id).first()
        
        if not entry:
            session.close()
            raise HTTPException(status_code=404, detail="Cache entry not found")
        
        # Force re-geocode
        service = get_geocoding_service()
        result = service.geocode_address(
            entry.street,
            entry.city,
            entry.state,
            entry.zip_code,
            force_refresh=True
        )
        
        session.close()
        
        return JSONResponse({
            "success": True,
            "message": "Cache entry refreshed",
            "data": result
        })
        
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


# =============================================================================
# FAMILY GEOCODING (Mirror Database Integration)
# =============================================================================

@router.get("/families", response_class=HTMLResponse)
async def families_geocoding_status(
    request: Request,
    campus_id: Optional[int] = Query(None),
    status: str = Query("all"),  # all, geocoded, pending, failed
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=100),
    user: dict = Depends(get_current_user)
):
    """View geocoding status for families in mirror database"""
    try:
        from mirror_database import Family, Campus, get_mirror_db
        from sqlalchemy import and_, or_
        
        db = get_mirror_db()
        session = db.session
        
        query = session.query(Family)
        
        # Campus filter
        if campus_id:
            query = query.filter(Family.campus_id == campus_id)
        
        # Status filter
        if status == "geocoded":
            query = query.filter(
                and_(
                    Family.latitude.isnot(None),
                    Family.longitude.isnot(None)
                )
            )
        elif status == "pending":
            query = query.filter(
                or_(
                    Family.latitude.is_(None),
                    Family.longitude.is_(None)
                )
            )
        elif status == "failed":
            query = query.filter(Family.geocode_status == "failed")
        
        # Get counts for stats
        total_families = session.query(Family).count()
        geocoded_count = session.query(Family).filter(
            and_(
                Family.latitude.isnot(None),
                Family.longitude.isnot(None)
            )
        ).count()
        
        # Paginate results
        total = query.count()
        offset = (page - 1) * per_page
        families = query.order_by(Family.family_name)\
            .offset(offset).limit(per_page).all()
        
        total_pages = (total + per_page - 1) // per_page
        
        # Get campuses for filter dropdown
        campuses = session.query(Campus).order_by(Campus.name).all()
        
        return templates.TemplateResponse("geocoding/families.html", {
            "request": request,
            "user": user,
            "families": families,
            "campuses": campuses,
            "campus_id": campus_id,
            "status": status,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "total_families": total_families,
            "geocoded_count": geocoded_count,
            "pending_count": total_families - geocoded_count
        })
        
    except Exception as e:
        return templates.TemplateResponse("geocoding/families.html", {
            "request": request,
            "user": user,
            "error": str(e),
            "families": [],
            "campuses": [],
            "campus_id": None,
            "status": "all",
            "page": 1,
            "per_page": per_page,
            "total": 0,
            "total_pages": 0,
            "total_families": 0,
            "geocoded_count": 0,
            "pending_count": 0
        })


@router.post("/families/geocode")
async def geocode_families(
    request: Request,
    user: dict = Depends(require_admin)
):
    """Batch geocode families that don't have coordinates (admin only)"""
    try:
        data = await request.json()
        campus_id = data.get("campus_id")
        limit = min(data.get("limit", 100), 500)  # Max 500 at a time
        
        from mirror_database import Family, get_mirror_db
        from sqlalchemy import and_, or_
        
        db = get_mirror_db()
        session = db.session
        
        query = session.query(Family).filter(
            or_(
                Family.latitude.is_(None),
                Family.longitude.is_(None)
            )
        )
        
        if campus_id:
            query = query.filter(Family.campus_id == campus_id)
        
        families = query.limit(limit).all()
        
        service = get_geocoding_service()
        results = {
            "total": len(families),
            "geocoded": 0,
            "cached": 0,
            "failed": 0,
            "errors": []
        }
        
        for family in families:
            try:
                # Build address from family record
                street = family.address_street or ""
                city = family.address_city or ""
                state = family.address_state or "WA"
                zip_code = family.address_zip or ""
                
                if not street or not city:
                    results["failed"] += 1
                    continue
                
                result = service.geocode_address(street, city, state, zip_code)
                
                if result:
                    family.latitude = result["lat"]
                    family.longitude = result["lng"]
                    family.geocode_status = "success"
                    family.geocoded_at = datetime.utcnow()
                    
                    if result.get("cached"):
                        results["cached"] += 1
                    else:
                        results["geocoded"] += 1
                else:
                    family.geocode_status = "failed"
                    results["failed"] += 1
                    
            except Exception as e:
                family.geocode_status = "error"
                results["failed"] += 1
                results["errors"].append(str(e))
        
        session.commit()
        
        return JSONResponse({
            "success": True,
            "results": results
        })
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


# =============================================================================
# MAP VISUALIZATION
# =============================================================================

@router.get("/map", response_class=HTMLResponse)
async def geocoding_map(
    request: Request,
    campus_id: Optional[int] = Query(None),
    user: dict = Depends(get_current_user)
):
    """Interactive map showing geocoded families"""
    try:
        from mirror_database import Campus, get_mirror_db
        
        db = get_mirror_db()
        session = db.session
        
        campuses = session.query(Campus).order_by(Campus.name).all()
        
        return templates.TemplateResponse("geocoding/map.html", {
            "request": request,
            "user": user,
            "campuses": campuses,
            "campus_id": campus_id,
            "google_maps_key": os.environ.get("GOOGLE_MAPS_API_KEY", "")
        })
        
    except Exception as e:
        return templates.TemplateResponse("geocoding/map.html", {
            "request": request,
            "user": user,
            "error": str(e),
            "campuses": [],
            "campus_id": None,
            "google_maps_key": ""
        })


@router.get("/api/map/families")
async def api_map_families(
    campus_id: Optional[int] = Query(None),
    user: dict = Depends(get_current_user)
):
    """API endpoint for getting family locations for map"""
    try:
        from mirror_database import Family, get_mirror_db
        from sqlalchemy import and_
        
        db = get_mirror_db()
        session = db.session
        
        query = session.query(Family).filter(
            and_(
                Family.latitude.isnot(None),
                Family.longitude.isnot(None)
            )
        )
        
        if campus_id:
            query = query.filter(Family.campus_id == campus_id)
        
        families = query.all()
        
        points = []
        for f in families:
            points.append({
                "id": f.family_id,
                "name": f.family_name,
                "lat": float(f.latitude),
                "lng": float(f.longitude),
                "address": f"{f.address_street}, {f.address_city}, {f.address_state} {f.address_zip}"
            })
        
        return JSONResponse({
            "success": True,
            "count": len(points),
            "data": points
        })
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


# =============================================================================
# STATISTICS & REPORTS
# =============================================================================

@router.get("/stats", response_class=HTMLResponse)
async def geocoding_stats(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Detailed geocoding statistics and cost analysis"""
    try:
        from geocoding_database import GeocodeCache, get_geocode_session
        from mirror_database import Family, Campus, get_mirror_db
        from sqlalchemy import func, and_, case
        
        # Cache stats
        geo_session = get_geocode_session()
        
        cache_stats = {
            "total": geo_session.query(GeocodeCache).count(),
            "total_usage": geo_session.query(func.sum(GeocodeCache.usage_count)).scalar() or 0
        }
        
        # Cost analysis (Google charges ~$5 per 1000 requests)
        cost_per_request = 0.005
        cache_stats["api_calls_saved"] = cache_stats["total_usage"] - cache_stats["total"]
        cache_stats["estimated_savings"] = cache_stats["api_calls_saved"] * cost_per_request
        cache_stats["total_cost"] = cache_stats["total"] * cost_per_request
        
        # Accuracy distribution
        accuracy_dist = geo_session.query(
            GeocodeCache.accuracy,
            func.count(GeocodeCache.id)
        ).group_by(GeocodeCache.accuracy).all()
        
        # Geocodes over time (by month) - MariaDB uses DATE_FORMAT
        monthly_geocodes = geo_session.query(
            func.date_format(GeocodeCache.geocoded_at, '%Y-%m').label('month'),
            func.count(GeocodeCache.id)
        ).filter(GeocodeCache.geocoded_at.isnot(None))\
         .group_by(func.date_format(GeocodeCache.geocoded_at, '%Y-%m'))\
         .order_by(func.date_format(GeocodeCache.geocoded_at, '%Y-%m').desc())\
         .limit(12).all()
        
        geo_session.close()
        
        # Family coverage stats
        db = get_mirror_db()
        session = db.session
        
        family_stats = {
            "total": session.query(Family).count(),
            "geocoded": session.query(Family).filter(
                and_(
                    Family.latitude.isnot(None),
                    Family.longitude.isnot(None)
                )
            ).count()
        }
        family_stats["pending"] = family_stats["total"] - family_stats["geocoded"]
        family_stats["coverage_pct"] = (family_stats["geocoded"] / family_stats["total"] * 100) if family_stats["total"] > 0 else 0
        
        # Per-campus breakdown
        campus_coverage = session.query(
            Campus.name,
            func.count(Family.family_id),
            func.sum(case((and_(Family.latitude.isnot(None), Family.longitude.isnot(None)), 1), else_=0))
        ).join(Family, Family.campus_id == Campus.campus_id)\
         .group_by(Campus.name)\
         .order_by(Campus.name).all()
        
        return templates.TemplateResponse("geocoding/stats.html", {
            "request": request,
            "user": user,
            "cache_stats": cache_stats,
            "accuracy_dist": accuracy_dist,
            "monthly_geocodes": monthly_geocodes,
            "family_stats": family_stats,
            "campus_coverage": campus_coverage
        })
        
    except Exception as e:
        import traceback
        return templates.TemplateResponse("geocoding/stats.html", {
            "request": request,
            "user": user,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "cache_stats": {},
            "accuracy_dist": [],
            "monthly_geocodes": [],
            "family_stats": {},
            "campus_coverage": []
        })
