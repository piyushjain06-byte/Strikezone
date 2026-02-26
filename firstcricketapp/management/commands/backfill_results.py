from django.core.management.base import BaseCommand
from firstcricketapp.models import CreateMatch, Innings, MatchResult


class Command(BaseCommand):

    def handle(self, *args, **kwargs):
        fixed = 0
        skipped = 0

        for match in CreateMatch.objects.all():
            if MatchResult.objects.filter(match=match).exists():
                self.stdout.write(f"SKIP Match {match.id} ({match}) - already exists")
                skipped += 1
                continue

            inn1 = Innings.objects.filter(match=match, innings_number=1).first()
            inn2 = Innings.objects.filter(match=match, innings_number=2).first()

            if not inn1 or not inn2 or inn2.status != 'COMPLETED':
                self.stdout.write(f"SKIP Match {match.id} ({match}) - not completed")
                skipped += 1
                continue

            if inn2.total_runs > inn1.total_runs:
                winner_team = inn2.batting_team
                result_type = "WIN_BY_WICKETS"
                win_margin = 10 - inn2.total_wickets
                result_summary = f"{winner_team.team_name} won by {win_margin} wickets"
            elif inn1.total_runs > inn2.total_runs:
                winner_team = inn1.batting_team
                result_type = "WIN_BY_RUNS"
                win_margin = inn1.total_runs - inn2.total_runs
                result_summary = f"{winner_team.team_name} won by {win_margin} runs"
            else:
                winner_team = None
                result_type = "TIE"
                win_margin = None
                result_summary = "Match Tied"

            MatchResult.objects.create(
                match=match,
                winner=winner_team,
                result_type=result_type,
                win_margin=win_margin,
                result_summary=result_summary,
            )
            self.stdout.write(f"FIXED Match {match.id} ({match}) - {result_summary}")
            fixed += 1

        self.stdout.write(f"\nDone! {fixed} result(s) created, {skipped} skipped.")