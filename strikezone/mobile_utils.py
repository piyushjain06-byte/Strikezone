# Mobile Responsiveness & PWA Support
"""
Progressive Web App (PWA) utilities
Allows users to install the web app on their phones like a native app
"""

PWA_MANIFEST = {
    "name": "StrikeZone Cricket",
    "short_name": "StrikeZone",
    "description": "Cricket Tournament Management & Live Scoring",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0a0f1e",
    "theme_color": "#f59e0b",
    "orientation": "portrait",
    "icons": [
        {
            "src": "/static/icons/icon-72x72.png",
            "sizes": "72x72",
            "type": "image/png"
        },
        {
            "src": "/static/icons/icon-96x96.png",
            "sizes": "96x96",
            "type": "image/png"
        },
        {
            "src": "/static/icons/icon-128x128.png",
            "sizes": "128x128",
            "type": "image/png"
        },
        {
            "src": "/static/icons/icon-144x144.png",
            "sizes": "144x144",
            "type": "image/png"
        },
        {
            "src": "/static/icons/icon-152x152.png",
            "sizes": "152x152",
            "type": "image/png"
        },
        {
            "src": "/static/icons/icon-192x192.png",
            "sizes": "192x192",
            "type": "image/png"
        },
        {
            "src": "/static/icons/icon-384x384.png",
            "sizes": "384x384",
            "type": "image/png"
        },
        {
            "src": "/static/icons/icon-512x512.png",
            "sizes": "512x512",
            "type": "image/png"
        }
    ]
}

SERVICE_WORKER_JS = """
// Service Worker for offline support
const CACHE_NAME = 'strikezone-v1';
const urlsToCache = [
  '/',
  '/static/css/style.css',
  '/static/js/main.js'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
  );
});

self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request)
      .then(response => response || fetch(event.request))
  );
});
"""


def is_mobile_request(request):
    """Detect if request is from mobile device"""
    user_agent = request.META.get('HTTP_USER_AGENT', '').lower()
    mobile_keywords = ['mobile', 'android', 'iphone', 'ipad', 'ipod', 'blackberry', 'windows phone']
    return any(keyword in user_agent for keyword in mobile_keywords)


def get_device_type(request):
    """Get detailed device type"""
    user_agent = request.META.get('HTTP_USER_AGENT', '').lower()
    
    if 'ipad' in user_agent or 'tablet' in user_agent:
        return 'tablet'
    elif any(x in user_agent for x in ['iphone', 'android', 'mobile']):
        return 'mobile'
    else:
        return 'desktop'
