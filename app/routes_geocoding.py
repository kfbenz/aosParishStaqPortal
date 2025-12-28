"""
ParishStaq Portal - Geocoding Routes
Provides web interface for geocoding operations using the mirror database.

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
sys.path.insert(0, '/opt/portal_app/aosParishStaq/src')

from .auth import get_current_user, require_admin

router = APIRouter(prefix="/geocoding", tags=["Geocoding"])

templates = Jinja2Templates(directory="templates")


# =============================================================================
# DATABASE HELPERS
# =============================================================================

def get_mirror_db():
    """Get MirrorDatabase instance"""
    from mirror_database import MirrorDatabase
    return MirrorDatabase()


# =============================================================================
# GEOCODING DASHBOARD
# =============================================================================

@router.get("/", response_class=HTMLResponse)
async def geocoding_dashboard(request: Request, user: dict = Depends(get_current_user)):
    """Main geocoding dashboard with stats and tools"""
    try:
        db = get_mirror_db()
        
        # Get overall stats
        stats = db.get_stats()
        
        # Get per-campus stats
        campus_stats = db.get_geocoding_stats_by_campus()
        
        # Get cache stats
        from mirror_database import GeocodingCache
        cache_total = db.session.query(GeocodingCache).count()
        cache_success = db.session.query(GeocodingCache).filter_by(geocode_status='success').count()
        cache_pending = db.session.query(GeocodingCache).filter_by(geocode_status='pending').count()
        cache_failed = db.session.query(GeocodingCache).filter_by(geocode_status='failed').count()
        
        db.close()
        
        return templates.TemplateResponse("geocoding/dashboard.html", {
            "request": request,
            "user": user,
            "stats": stats,
            "campus_stats": campus_stats,
            "cache_stats": {
                "total": cache_total,
                "success": cache_success,
                "pending": cache_pending,
                "failed": cache_failed
            }
        })
        
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "user": user,
            "error": "Geocoding Dashboard Error",
            "message": str(e)
        }, status_code=500)


# =============================================================================
# ADDRESS LOOKUP
# =============================================================================

@router.get("/lookup", response_class=HTMLResponse)
async def geocode_lookup_form(request: Request, user: dict = Depends(get_current_user)):
    """Show geocode lookup form"""
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
    zip_code: str = Form(""),
    user: dict = Depends(get_current_user)
):
    """Look up or geocode an address"""
    try:
        db = get_mirror_db()
        
        # Check cache first
        cached = db.get_geocode(street, city, state, zip_code)
        
        result = None
        from_cache = False
        
        if cached:
            result = {
                "latitude": cached.latitude,
                "longitude": cached.longitude,
                "source": cached.geocode_source,
                "quality": cached.geocode_quality,
                "status": cached.geocode_status,
                "formatted_address": cached.formatted_address,
                "geocoded_at": cached.geocoded_at
            }
            from_cache = True
        
        db.close()
        
        return templates.TemplateResponse("geocoding/lookup.html", {
            "request": request,
            "user": user,
            "result": result,
            "from_cache": from_cache,
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
# CACHE MANAGEMENT
# =============================================================================

@router.get("/cache", response_class=HTMLResponse)
async def geocode_cache(
    request: Request,
    status: str = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    user: dict = Depends(get_current_user)
):
    """Browse geocoding cache"""
    try:
        db = get_mirror_db()
        from mirror_database import GeocodingCache
        
        # Build query
        query = db.session.query(GeocodingCache)
        
        if status:
            query = query.filter(GeocodingCache.geocode_status == status)
        
        # Get total count
        total = query.count()
        
        # Paginate
        offset = (page - 1) * per_page
        records = query.order_by(GeocodingCache.updated_at.desc()).offset(offset).limit(per_page).all()
        
        total_pages = (total + per_page - 1) // per_page
        
        db.close()
        
        return templates.TemplateResponse("geocoding/cache.html", {
            "request": request,
            "user": user,
            "records": records,
            "status_filter": status,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages
        })
        
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "user": user,
            "error": "Cache Error",
            "message": str(e)
        }, status_code=500)


@router.get("/cache/{cache_id}", response_class=HTMLResponse)
async def geocode_cache_detail(
    request: Request,
    cache_id: int,
    user: dict = Depends(get_current_user)
):
    """View geocode cache record detail"""
    try:
        db = get_mirror_db()
        from mirror_database import GeocodingCache
        
        record = db.session.query(GeocodingCache).filter_by(id=cache_id).first()
        
        if not record:
            db.close()
            raise HTTPException(status_code=404, detail="Cache record not found")
        
        db.close()
        
        return templates.TemplateResponse("geocoding/cache_detail.html", {
            "request": request,
            "user": user,
            "record": record
        })
        
    except HTTPException:
        raise
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "user": user,
            "error": "Cache Detail Error",
            "message": str(e)
        }, status_code=500)


# =============================================================================
# FAMILIES / INDIVIDUALS GEOCODING
# =============================================================================

@router.get("/families", response_class=HTMLResponse)
async def geocode_families(
    request: Request,
    campus_id: int = Query(None),
    status: str = Query(None),  # geocoded, pending, all
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    user: dict = Depends(get_current_user)
):
    """Browse individuals needing or having geocoding"""
    try:
        db = get_mirror_db()
        from mirror_database import Individual, Campus
        
        # Get campuses for filter dropdown
        campuses = db.session.query(Campus).filter_by(active=True).order_by(Campus.name).all()
        
        # Build query - household heads only
        query = db.session.query(Individual).filter(
            Individual.household_position == 'PRIMARY_CONTACT',
            Individual.active == True
        )
        
        if campus_id:
            query = query.filter(Individual.campus_id == campus_id)
        
        if status == 'geocoded':
            query = query.filter(
                Individual.latitude.isnot(None),
                Individual.latitude != ''
            )
        elif status == 'pending':
            query = query.filter(
                Individual.address_street.isnot(None),
                Individual.address_street != '',
                (Individual.latitude.is_(None)) | (Individual.latitude == '')
            )
        
        # Get total count
        total = query.count()
        
        # Paginate
        offset = (page - 1) * per_page
        individuals = query.order_by(Individual.last_name, Individual.first_name).offset(offset).limit(per_page).all()
        
        total_pages = (total + per_page - 1) // per_page
        
        db.close()
        
        return templates.TemplateResponse("geocoding/families.html", {
            "request": request,
            "user": user,
            "individuals": individuals,
            "campuses": campuses,
            "campus_id": campus_id,
            "status_filter": status,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages
        })
        
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "user": user,
            "error": "Families Error",
            "message": str(e)
        }, status_code=500)


# =============================================================================
# MAP VIEW
# =============================================================================

@router.get("/map", response_class=HTMLResponse)
async def geocode_map(
    request: Request,
    campus_id: int = Query(None),
    user: dict = Depends(get_current_user)
):
    """Map view of geocoded individuals"""
    try:
        db = get_mirror_db()
        from mirror_database import Individual, Campus
        
        # Get campuses for filter
        campuses = db.session.query(Campus).filter_by(active=True).order_by(Campus.name).all()
        
        # Get geocoded individuals (limit for performance)
        query = db.session.query(Individual).filter(
            Individual.household_position == 'PRIMARY_CONTACT',
            Individual.active == True,
            Individual.latitude.isnot(None),
            Individual.latitude != '',
            Individual.longitude.isnot(None),
            Individual.longitude != ''
        )
        
        if campus_id:
            query = query.filter(Individual.campus_id == campus_id)
        
        # Limit to 1000 for performance
        individuals = query.limit(1000).all()
        
        # Build marker data
        markers = []
        for ind in individuals:
            try:
                markers.append({
                    "lat": float(ind.latitude),
                    "lng": float(ind.longitude),
                    "name": f"{ind.first_name} {ind.last_name}",
                    "address": ind.full_address,
                    "aos_id": ind.aos_id
                })
            except (ValueError, TypeError):
                continue
        
        db.close()
        
        # Get Google Maps API key from environment
        google_maps_key = os.environ.get('GOOGLE_MAPS_API_KEY', '')
        
        return templates.TemplateResponse("geocoding/map.html", {
            "request": request,
            "user": user,
            "markers": markers,
            "campuses": campuses,
            "campus_id": campus_id,
            "google_maps_key": google_maps_key,
            "total_markers": len(markers)
        })
        
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "user": user,
            "error": "Map Error",
            "message": str(e)
        }, status_code=500)


# =============================================================================
# STATISTICS
# =============================================================================

@router.get("/stats", response_class=HTMLResponse)
async def geocode_stats(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Detailed geocoding statistics"""
    try:
        db = get_mirror_db()
        from mirror_database import Individual, GeocodingCache, Campus
        from sqlalchemy import func
        
        # Overall individual stats
        total_heads = db.session.query(Individual).filter(
            Individual.household_position == 'PRIMARY_CONTACT',
            Individual.active == True
        ).count()
        
        geocoded_heads = db.session.query(Individual).filter(
            Individual.household_position == 'PRIMARY_CONTACT',
            Individual.active == True,
            Individual.latitude.isnot(None),
            Individual.latitude != ''
        ).count()
        
        pending_heads = db.session.query(Individual).filter(
            Individual.household_position == 'PRIMARY_CONTACT',
            Individual.active == True,
            Individual.address_street.isnot(None),
            Individual.address_street != '',
            (Individual.latitude.is_(None)) | (Individual.latitude == '')
        ).count()
        
        # Cache stats
        cache_total = db.session.query(GeocodingCache).count()
        
        cache_by_status = db.session.query(
            GeocodingCache.geocode_status,
            func.count(GeocodingCache.id)
        ).group_by(GeocodingCache.geocode_status).all()
        
        cache_by_source = db.session.query(
            GeocodingCache.geocode_source,
            func.count(GeocodingCache.id)
        ).filter(
            GeocodingCache.geocode_status == 'success'
        ).group_by(GeocodingCache.geocode_source).all()
        
        cache_by_quality = db.session.query(
            GeocodingCache.geocode_quality,
            func.count(GeocodingCache.id)
        ).filter(
            GeocodingCache.geocode_status == 'success'
        ).group_by(GeocodingCache.geocode_quality).all()
        
        # Per-campus breakdown
        campus_stats = db.get_geocoding_stats_by_campus()
        
        db.close()
        
        return templates.TemplateResponse("geocoding/stats.html", {
            "request": request,
            "user": user,
            "total_heads": total_heads,
            "geocoded_heads": geocoded_heads,
            "pending_heads": pending_heads,
            "percent_geocoded": round(geocoded_heads / total_heads * 100, 1) if total_heads > 0 else 0,
            "cache_total": cache_total,
            "cache_by_status": dict(cache_by_status),
            "cache_by_source": dict(cache_by_source),
            "cache_by_quality": dict(cache_by_quality),
            "campus_stats": campus_stats
        })
        
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "user": user,
            "error": "Statistics Error",
            "message": str(e)
        }, status_code=500)


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
    """API endpoint for looking up geocode from cache"""
    try:
        db = get_mirror_db()
        cached = db.get_geocode(street, city, state, zip_code)
        
        if cached and cached.geocode_status == 'success':
            result = {
                "success": True,
                "from_cache": True,
                "data": {
                    "latitude": cached.latitude,
                    "longitude": cached.longitude,
                    "source": cached.geocode_source,
                    "quality": cached.geocode_quality,
                    "formatted_address": cached.formatted_address
                }
            }
        else:
            result = {
                "success": False,
                "from_cache": cached is not None,
                "error": "Address not found in cache" if not cached else f"Status: {cached.geocode_status}"
            }
        
        db.close()
        return JSONResponse(result)
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


@router.get("/api/stats")
async def api_stats(user: dict = Depends(get_current_user)):
    """API endpoint for geocoding stats"""
    try:
        db = get_mirror_db()
        stats = db.get_stats()
        campus_stats = db.get_geocoding_stats_by_campus()
        db.close()
        
        return JSONResponse({
            "success": True,
            "overall": stats,
            "by_campus": campus_stats
        })
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


@router.get("/api/markers")
async def api_markers(
    campus_id: int = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
    user: dict = Depends(get_current_user)
):
    """API endpoint for map markers"""
    try:
        db = get_mirror_db()
        from mirror_database import Individual
        
        query = db.session.query(Individual).filter(
            Individual.household_position == 'PRIMARY_CONTACT',
            Individual.active == True,
            Individual.latitude.isnot(None),
            Individual.latitude != '',
            Individual.longitude.isnot(None),
            Individual.longitude != ''
        )
        
        if campus_id:
            query = query.filter(Individual.campus_id == campus_id)
        
        individuals = query.limit(limit).all()
        
        markers = []
        for ind in individuals:
            try:
                markers.append({
                    "lat": float(ind.latitude),
                    "lng": float(ind.longitude),
                    "name": f"{ind.first_name} {ind.last_name}",
                    "address": ind.full_address,
                    "aos_id": ind.aos_id
                })
            except (ValueError, TypeError):
                continue
        
        db.close()
        
        return JSONResponse({
            "success": True,
            "count": len(markers),
            "markers": markers
        })
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)
