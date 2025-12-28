"""
ParishStaq Geocoding Service

Geocoding service with permanent caching using Google Maps API.
Designed to minimize API costs by caching all results permanently.

Author: Kevin Benz - Archdiocese of Seattle IT
Date: December 2024
"""

import os
import re
import json
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

import googlemaps
from googlemaps.exceptions import ApiError, Timeout, TransportError

from geocoding_database import GeocodeCache, get_geocode_session

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GeocodingService:
    """
    Geocoding service with permanent caching.
    
    Features:
    - Google Maps API integration
    - Permanent SQLite cache to avoid re-geocoding
    - Address normalization for consistent cache hits
    - Rate limiting to respect API quotas
    - Detailed accuracy and confidence metrics
    - Full API response storage for audit trail
    """
    
    # Rate limiting (Google allows 50 QPS, we'll be conservative)
    RATE_LIMIT_DELAY = 0.05  # 50ms between requests = 20 QPS max
    
    # Confidence mapping based on location_type
    CONFIDENCE_MAP = {
        'ROOFTOP': 'high',
        'RANGE_INTERPOLATED': 'medium',
        'GEOMETRIC_CENTER': 'medium',
        'APPROXIMATE': 'low'
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the geocoding service.
        
        Args:
            api_key: Google Maps API key. If not provided, reads from
                     GOOGLE_MAPS_API_KEY environment variable.
        """
        self.api_key = api_key or os.environ.get('GOOGLE_MAPS_API_KEY')
        
        if not self.api_key:
            raise ValueError(
                "Google Maps API key required. Set GOOGLE_MAPS_API_KEY "
                "environment variable or pass api_key parameter."
            )
        
        self.gmaps = googlemaps.Client(key=self.api_key)
        self._last_request_time = 0
    
    def _normalize_address_key(
        self, 
        street: str, 
        city: str, 
        state: str, 
        zip_code: str
    ) -> str:
        """
        Create a normalized cache key from address components.
        
        Normalization:
        - Uppercase all text
        - Remove extra whitespace
        - Standardize common abbreviations
        - Remove apartment/unit numbers (they don't affect coordinates)
        """
        # Uppercase and strip
        street = (street or '').upper().strip()
        city = (city or '').upper().strip()
        state = (state or '').upper().strip()
        zip_code = (zip_code or '').strip()
        
        # Remove unit/apt numbers from street (coordinates are the same)
        street = re.sub(r'\s*(APT|UNIT|#|STE|SUITE|BLDG|BUILDING)\s*[\w\-]+\s*$', '', street, flags=re.IGNORECASE)
        
        # Standardize common abbreviations
        replacements = {
            r'\bSTREET\b': 'ST',
            r'\bAVENUE\b': 'AVE',
            r'\bBOULEVARD\b': 'BLVD',
            r'\bDRIVE\b': 'DR',
            r'\bLANE\b': 'LN',
            r'\bCOURT\b': 'CT',
            r'\bPLACE\b': 'PL',
            r'\bROAD\b': 'RD',
            r'\bCIRCLE\b': 'CIR',
            r'\bNORTH\b': 'N',
            r'\bSOUTH\b': 'S',
            r'\bEAST\b': 'E',
            r'\bWEST\b': 'W',
            r'\bNORTHEAST\b': 'NE',
            r'\bNORTHWEST\b': 'NW',
            r'\bSOUTHEAST\b': 'SE',
            r'\bSOUTHWEST\b': 'SW',
        }
        
        for pattern, replacement in replacements.items():
            street = re.sub(pattern, replacement, street)
        
        # Remove extra whitespace
        street = ' '.join(street.split())
        city = ' '.join(city.split())
        
        # Take only first 5 digits of zip
        if zip_code:
            zip_code = zip_code[:5]
        
        return f"{street}|{city}|{state}|{zip_code}"
    
    def _rate_limit(self):
        """Enforce rate limiting between API calls"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()
    
    def geocode_address(
        self,
        street: str,
        city: str,
        state: str = 'WA',
        zip_code: str = '',
        force_refresh: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Geocode an address with caching.
        
        Args:
            street: Street address
            city: City name
            state: State abbreviation (default: WA)
            zip_code: ZIP code
            force_refresh: If True, bypass cache and re-geocode
            
        Returns:
            Dictionary with geocoding results:
            - lat: Latitude
            - lng: Longitude
            - accuracy: Location accuracy type
            - confidence: Confidence level (high/medium/low)
            - formatted_address: Standardized address from Google
            - place_id: Google Place ID
            - cached: Whether result came from cache
            
            Returns None if geocoding fails.
        """
        session = get_geocode_session()
        
        try:
            # Create cache key
            address_key = self._normalize_address_key(street, city, state, zip_code)
            
            # Check cache first (unless force_refresh)
            if not force_refresh:
                cached = session.query(GeocodeCache).filter_by(
                    address_key=address_key
                ).first()
                
                if cached and cached.is_valid:
                    # Cache hit - update usage stats
                    cached.usage_count = (cached.usage_count or 0) + 1
                    cached.last_used_at = datetime.utcnow()
                    session.commit()
                    
                    logger.debug(f"Cache hit for: {address_key}")
                    
                    return {
                        'lat': cached.latitude,
                        'lng': cached.longitude,
                        'accuracy': cached.accuracy,
                        'confidence': cached.confidence,
                        'formatted_address': cached.formatted_address,
                        'place_id': cached.place_id,
                        'cached': True,
                        'cache_id': cached.id
                    }
            
            # Cache miss or force refresh - call Google API
            full_address = f"{street}, {city}, {state} {zip_code}".strip()
            
            logger.info(f"Geocoding: {full_address}")
            
            # Rate limit
            self._rate_limit()
            
            # Call Google Geocoding API
            try:
                results = self.gmaps.geocode(
                    address=full_address,
                    components={'country': 'US'},
                    region='us'
                )
            except (ApiError, Timeout, TransportError) as e:
                logger.error(f"Google API error: {e}")
                self._record_error(session, address_key, street, city, state, zip_code, str(e))
                return None
            
            if not results:
                logger.warning(f"No results for: {full_address}")
                self._record_error(session, address_key, street, city, state, zip_code, "No results")
                return None
            
            # Parse the first result
            result = results[0]
            location = result['geometry']['location']
            location_type = result['geometry'].get('location_type', 'UNKNOWN')
            
            # Calculate confidence
            confidence = self.CONFIDENCE_MAP.get(location_type, 'low')
            
            # Check if we're updating an existing entry or creating new
            cache_entry = session.query(GeocodeCache).filter_by(
                address_key=address_key
            ).first()
            
            if cache_entry:
                # Update existing entry
                cache_entry.latitude = location['lat']
                cache_entry.longitude = location['lng']
                cache_entry.formatted_address = result.get('formatted_address')
                cache_entry.place_id = result.get('place_id')
                cache_entry.accuracy = location_type
                cache_entry.location_type = location_type
                cache_entry.confidence = confidence
                cache_entry.geocoded_at = datetime.utcnow()
                cache_entry.api_response_json = json.dumps(result)
                cache_entry.error_message = None
                cache_entry.usage_count = (cache_entry.usage_count or 0) + 1
                cache_entry.last_used_at = datetime.utcnow()
                cache_entry.updated_at = datetime.utcnow()
            else:
                # Create new entry
                cache_entry = GeocodeCache(
                    address_key=address_key,
                    street=street,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    latitude=location['lat'],
                    longitude=location['lng'],
                    formatted_address=result.get('formatted_address'),
                    place_id=result.get('place_id'),
                    accuracy=location_type,
                    location_type=location_type,
                    confidence=confidence,
                    geocode_provider='google_maps',
                    geocoded_at=datetime.utcnow(),
                    api_response_json=json.dumps(result),
                    usage_count=1,
                    last_used_at=datetime.utcnow()
                )
                session.add(cache_entry)
            
            session.commit()
            
            return {
                'lat': location['lat'],
                'lng': location['lng'],
                'accuracy': location_type,
                'confidence': confidence,
                'formatted_address': result.get('formatted_address'),
                'place_id': result.get('place_id'),
                'cached': False,
                'cache_id': cache_entry.id
            }
            
        except Exception as e:
            logger.exception(f"Geocoding error: {e}")
            session.rollback()
            return None
            
        finally:
            session.close()
    
    def _record_error(
        self,
        session,
        address_key: str,
        street: str,
        city: str,
        state: str,
        zip_code: str,
        error_message: str
    ):
        """Record a geocoding error in the cache"""
        try:
            cache_entry = session.query(GeocodeCache).filter_by(
                address_key=address_key
            ).first()
            
            if cache_entry:
                cache_entry.error_message = error_message
                cache_entry.retry_count = (cache_entry.retry_count or 0) + 1
                cache_entry.last_error_at = datetime.utcnow()
            else:
                cache_entry = GeocodeCache(
                    address_key=address_key,
                    street=street,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    error_message=error_message,
                    retry_count=1,
                    last_error_at=datetime.utcnow()
                )
                session.add(cache_entry)
            
            session.commit()
        except:
            session.rollback()
    
    def batch_geocode(
        self,
        addresses: List[Dict[str, str]],
        progress_callback=None
    ) -> List[Dict[str, Any]]:
        """
        Geocode multiple addresses.
        
        Args:
            addresses: List of address dictionaries with keys:
                      street, city, state (optional), zip_code (optional)
            progress_callback: Optional callback(current, total) for progress
            
        Returns:
            List of geocoding results (same order as input)
        """
        results = []
        total = len(addresses)
        
        for i, addr in enumerate(addresses):
            result = self.geocode_address(
                street=addr.get('street', ''),
                city=addr.get('city', ''),
                state=addr.get('state', 'WA'),
                zip_code=addr.get('zip_code', '')
            )
            
            results.append({
                'input': addr,
                'result': result
            })
            
            if progress_callback:
                progress_callback(i + 1, total)
        
        return results
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get statistics about the geocoding cache"""
        session = get_geocode_session()
        
        try:
            from sqlalchemy import func
            
            total = session.query(GeocodeCache).count()
            valid = session.query(GeocodeCache).filter(
                GeocodeCache.latitude.isnot(None)
            ).count()
            failed = session.query(GeocodeCache).filter(
                GeocodeCache.error_message.isnot(None)
            ).count()
            
            total_usage = session.query(
                func.sum(GeocodeCache.usage_count)
            ).scalar() or 0
            
            accuracy_breakdown = dict(
                session.query(
                    GeocodeCache.accuracy,
                    func.count(GeocodeCache.id)
                ).filter(
                    GeocodeCache.accuracy.isnot(None)
                ).group_by(GeocodeCache.accuracy).all()
            )
            
            return {
                'total_cached': total,
                'valid': valid,
                'failed': failed,
                'total_lookups': total_usage,
                'api_calls_saved': total_usage - total,
                'accuracy_breakdown': accuracy_breakdown,
                'estimated_cost': total * 0.005,  # $5 per 1000 requests
                'estimated_savings': (total_usage - total) * 0.005
            }
            
        finally:
            session.close()


# =============================================================================
# CLI INTERFACE
# =============================================================================

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Geocoding Service CLI')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Geocode single address
    geocode_parser = subparsers.add_parser('geocode', help='Geocode an address')
    geocode_parser.add_argument('--street', '-s', required=True, help='Street address')
    geocode_parser.add_argument('--city', '-c', required=True, help='City')
    geocode_parser.add_argument('--state', default='WA', help='State (default: WA)')
    geocode_parser.add_argument('--zip', '-z', default='', help='ZIP code')
    geocode_parser.add_argument('--force', '-f', action='store_true', help='Force refresh')
    
    # Stats
    stats_parser = subparsers.add_parser('stats', help='Show cache statistics')
    
    args = parser.parse_args()
    
    if args.command == 'geocode':
        service = GeocodingService()
        result = service.geocode_address(
            street=args.street,
            city=args.city,
            state=args.state,
            zip_code=args.zip,
            force_refresh=args.force
        )
        
        if result:
            print(f"\nGeocoding Result:")
            print(f"  Latitude:  {result['lat']}")
            print(f"  Longitude: {result['lng']}")
            print(f"  Accuracy:  {result['accuracy']}")
            print(f"  Confidence: {result['confidence']}")
            print(f"  Address:   {result['formatted_address']}")
            print(f"  Cached:    {'Yes' if result['cached'] else 'No (new API call)'}")
        else:
            print("Geocoding failed")
            
    elif args.command == 'stats':
        service = GeocodingService()
        stats = service.get_cache_stats()
        
        print(f"\nGeocoding Cache Statistics")
        print(f"=" * 40)
        print(f"Total cached:    {stats['total_cached']:,}")
        print(f"Valid results:   {stats['valid']:,}")
        print(f"Failed:          {stats['failed']:,}")
        print(f"Total lookups:   {stats['total_lookups']:,}")
        print(f"API calls saved: {stats['api_calls_saved']:,}")
        print(f"\nEstimated API cost:    ${stats['estimated_cost']:.2f}")
        print(f"Estimated savings:     ${stats['estimated_savings']:.2f}")
        print(f"\nAccuracy breakdown:")
        for acc, count in stats['accuracy_breakdown'].items():
            print(f"  {acc}: {count:,}")
    
    else:
        parser.print_help()
