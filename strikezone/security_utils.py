# Security & Polish
from django.core.cache import cache
from django.http import HttpResponseForbidden
from functools import wraps
import time


def rate_limit(key_prefix, limit=10, period=60):
    """
    Rate limiting decorator
    Usage: @rate_limit('api_call', limit=10, period=60)
    Allows 10 requests per 60 seconds
    """
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            # Create unique key for this user/IP
            if request.user.is_authenticated:
                identifier = f"user_{request.user.id}"
            else:
                identifier = request.META.get('REMOTE_ADDR', 'unknown')
            
            cache_key = f"rate_limit_{key_prefix}_{identifier}"
            
            # Get current request count
            requests = cache.get(cache_key, [])
            now = time.time()
            
            # Remove old requests outside the time window
            requests = [req_time for req_time in requests if now - req_time < period]
            
            if len(requests) >= limit:
                return HttpResponseForbidden("Rate limit exceeded. Please try again later.")
            
            # Add current request
            requests.append(now)
            cache.set(cache_key, requests, period)
            
            return func(request, *args, **kwargs)
        return wrapper
    return decorator


def validate_phone_number(phone):
    """Validate phone number format"""
    import re
    # Remove spaces, dashes, parentheses
    phone = re.sub(r'[\s\-\(\)]', '', phone)
    
    # Check if it's a valid format (10 digits for India)
    if re.match(r'^[6-9]\d{9}$', phone):
        return True, phone
    
    # Check for international format
    if re.match(r'^\+91[6-9]\d{9}$', phone):
        return True, phone[3:]  # Remove +91
    
    return False, None


def sanitize_input(text, max_length=500):
    """Sanitize user input to prevent XSS"""
    import html
    
    # Escape HTML
    text = html.escape(text)
    
    # Limit length
    if len(text) > max_length:
        text = text[:max_length]
    
    # Remove potentially dangerous patterns
    dangerous_patterns = [
        '<script', '</script>',
        'javascript:', 'onerror=',
        'onclick=', 'onload='
    ]
    
    for pattern in dangerous_patterns:
        text = text.replace(pattern, '')
    
    return text.strip()


def check_csrf_token(request):
    """Additional CSRF validation"""
    from django.middleware.csrf import get_token
    
    if request.method in ['POST', 'PUT', 'DELETE', 'PATCH']:
        token = request.META.get('HTTP_X_CSRFTOKEN') or request.POST.get('csrfmiddlewaretoken')
        expected_token = get_token(request)
        
        if not token or token != expected_token:
            return False
    
    return True


def log_suspicious_activity(request, activity_type, details):
    """Log suspicious activities for security monitoring"""
    import logging
    
    logger = logging.getLogger('security')
    
    log_data = {
        'timestamp': time.time(),
        'activity_type': activity_type,
        'ip': request.META.get('REMOTE_ADDR'),
        'user_agent': request.META.get('HTTP_USER_AGENT'),
        'user': request.user.username if request.user.is_authenticated else 'anonymous',
        'details': details
    }
    
    logger.warning(f"Suspicious activity: {log_data}")


def validate_file_upload(file, allowed_extensions=['jpg', 'jpeg', 'png'], max_size_mb=5):
    """Validate uploaded files"""
    # Check file extension
    ext = file.name.split('.')[-1].lower()
    if ext not in allowed_extensions:
        return False, f"File type not allowed. Allowed: {', '.join(allowed_extensions)}"
    
    # Check file size
    if file.size > max_size_mb * 1024 * 1024:
        return False, f"File too large. Maximum size: {max_size_mb}MB"
    
    # Check if it's actually an image (for image uploads)
    if ext in ['jpg', 'jpeg', 'png', 'gif']:
        try:
            from PIL import Image
            img = Image.open(file)
            img.verify()
        except Exception:
            return False, "Invalid image file"
    
    return True, "Valid file"


def generate_secure_token(length=32):
    """Generate secure random token"""
    import secrets
    return secrets.token_urlsafe(length)


def hash_password_secure(password):
    """Hash password securely (for guest users)"""
    from django.contrib.auth.hashers import make_password
    return make_password(password)


def verify_password_secure(password, hashed):
    """Verify hashed password"""
    from django.contrib.auth.hashers import check_password
    return check_password(password, hashed)


# Input validation schemas
VALIDATION_RULES = {
    'player_name': {
        'min_length': 2,
        'max_length': 100,
        'pattern': r'^[a-zA-Z\s\.]+$',
        'error': 'Player name must be 2-100 characters, letters only'
    },
    'team_name': {
        'min_length': 2,
        'max_length': 100,
        'pattern': r'^[a-zA-Z0-9\s]+$',
        'error': 'Team name must be 2-100 characters, letters and numbers only'
    },
    'venue': {
        'min_length': 3,
        'max_length': 200,
        'pattern': r'^[a-zA-Z0-9\s\,\.\-]+$',
        'error': 'Venue must be 3-200 characters'
    },
    'mobile': {
        'pattern': r'^[6-9]\d{9}$',
        'error': 'Invalid mobile number. Must be 10 digits starting with 6-9'
    }
}


def validate_field(field_name, value):
    """Validate field against rules"""
    import re
    
    if field_name not in VALIDATION_RULES:
        return True, value
    
    rules = VALIDATION_RULES[field_name]
    
    # Check length
    if 'min_length' in rules and len(value) < rules['min_length']:
        return False, rules['error']
    
    if 'max_length' in rules and len(value) > rules['max_length']:
        return False, rules['error']
    
    # Check pattern
    if 'pattern' in rules and not re.match(rules['pattern'], value):
        return False, rules['error']
    
    return True, value
