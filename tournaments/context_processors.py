from tournaments.models import UpperCategory

def get_categories(request):
    categories= UpperCategory.objects.all()
    return dict(categories= categories)