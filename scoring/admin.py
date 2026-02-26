from django.contrib import admin


from django.contrib import admin
from .models import Innings
from firstcricketapp.models import TeamDetails


class InningsAdmin(admin.ModelAdmin):
    def formfield_for_foreignkey(self, db_field, request, **kwargs):

        if db_field.name in ["batting_team", "bowling_team"]:

            match_start_id = request.GET.get("match_start")

            if match_start_id:
                from firstcricketapp.models import MatchStart
                match_start = MatchStart.objects.get(id=match_start_id)
                match = match_start.match

                # Only show the 2 teams of this match
                kwargs["queryset"] = TeamDetails.objects.filter(
                    id__in=[match.team1.id, match.team2.id]
                )

        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    readonly_fields = ("batting_team", "bowling_team", "total_runs", "total_wickets", "total_overs")

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Innings, InningsAdmin)


