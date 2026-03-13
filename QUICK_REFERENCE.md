# 🚀 Quick Reference Card

## 📍 New URLs

| Feature | URL | Description |
|---------|-----|-------------|
| 🔍 Search | `/search/` | Search everything |
| ⚔️ Compare | `/player-comparison/` | Compare 2 players |
| 📱 Install | `/install-app/` | Install as app |
| 📄 PDF | `/match/<id>/export-pdf/` | Download PDF |
| 📊 CSV | `/tournament/<id>/export-csv/` | Download CSV |
| 📅 Calendar | `/tournament/<id>/calendar/` | Match schedule |

## 🔌 API Endpoints

```bash
# Search
GET /api/search-enhanced/?q=virat

# Player Comparison
GET /api/player-comparison/?p1=1&p2=2

# Player Form
GET /api/player/<id>/form/

# Team H2H
GET /api/team-h2h/<team1_id>/<team2_id>/

# Bulk Upload
POST /admin/bulk-upload-players/
```

## 💻 Code Examples

### Search
```python
from strikezone.search_utils import global_search
results = global_search('virat', limit=10)
```

### Analytics
```python
from strikezone.analytics_utils import get_player_form
form = get_player_form(player_id=1, last_n_matches=5)
```

### Caching
```python
from strikezone.performance_utils import cache_result

@cache_result(timeout=600)
def my_function():
    return expensive_query()
```

### Rate Limiting
```python
from strikezone.security_utils import rate_limit

@rate_limit('api', limit=10, period=60)
def my_view(request):
    return JsonResponse({'data': 'value'})
```

### Validation
```python
from strikezone.security_utils import validate_field
is_valid, value = validate_field('player_name', 'Virat Kohli')
```

## 📦 Files Created

```
strikezone/
├── performance_utils.py    # Caching & optimization
├── search_utils.py          # Search functionality
├── analytics_utils.py       # Analytics & insights
├── mobile_utils.py          # PWA support
├── social_utils.py          # Social features
├── admin_utils.py           # Admin tools
├── security_utils.py        # Security
└── views_enhanced.py        # New views

templates/
├── search_results.html      # Search page
├── player_comparison.html   # Comparison page
├── install_pwa.html         # PWA install
├── 404.html                 # Not found
└── 500.html                 # Server error
```

## 🎯 Quick Setup

```bash
# 1. Install dependency
pip install reportlab

# 2. Test locally
python manage.py runserver

# 3. Visit new pages
http://localhost:8000/search/
http://localhost:8000/player-comparison/
http://localhost:8000/install-app/

# 4. Deploy
git add .
git commit -m "Added features"
git push
```

## 🔧 Configuration

### Enable Caching (settings.py)
```python
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'strikezone-cache',
    }
}
```

### Custom Rate Limits
```python
@rate_limit('api_call', limit=20, period=60)  # 20/min
```

### Cache Duration
```python
@cache_result(timeout=300)  # 5 minutes
```

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| Search empty | Add data to database |
| PDF fails | `pip install reportlab` |
| PWA not installing | Use HTTPS or localhost |
| Rate limit strict | Increase limit in decorator |
| Import error | Check file locations |

## 📊 Performance Tips

1. ✅ Enable caching
2. ✅ Use optimized queries
3. ✅ Compress images
4. ✅ Enable PWA caching
5. ✅ Monitor rate limits

## 🎨 Customization

### PWA Colors
```python
# mobile_utils.py
PWA_MANIFEST = {
    "theme_color": "#f59e0b",  # Change
    "background_color": "#0a0f1e",  # Change
}
```

### Validation Rules
```python
# security_utils.py
VALIDATION_RULES = {
    'player_name': {
        'min_length': 2,
        'max_length': 100,
        # Customize...
    }
}
```

## ✅ Testing Checklist

- [ ] Search works
- [ ] Comparison works
- [ ] PWA installs
- [ ] PDF exports
- [ ] CSV exports
- [ ] Rate limiting active
- [ ] Error pages show
- [ ] Mobile responsive

## 📱 Mobile Testing

```bash
# Find your IP
ipconfig  # Windows
ifconfig  # Mac/Linux

# Access from phone
http://YOUR_IP:8000
```

## 🚀 Deploy Commands

```bash
# Render/Railway auto-deploys on push
git add .
git commit -m "New features"
git push

# Manual deploy
python manage.py collectstatic
python manage.py migrate
gunicorn strikezone.wsgi
```

## 📞 Quick Help

- Full docs: `FEATURES_ADDED.md`
- Setup guide: `SETUP_NEW_FEATURES.md`
- Summary: `IMPLEMENTATION_SUMMARY.md`
- This card: `QUICK_REFERENCE.md`

---

**Keep this card handy for quick reference! 📌**
