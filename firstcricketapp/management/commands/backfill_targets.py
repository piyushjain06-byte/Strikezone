from django.core.management.base import BaseCommand
from firstcricketapp.models import Innings


class Command(BaseCommand):

    def handle(self, *args, **kwargs):
        fixed = 0
        skipped = 0

        for inn2 in Innings.objects.filter(innings_number=2):
            if inn2.target is not None:
                self.stdout.write(f"SKIP Match {inn2.match.id} - target already set ({inn2.target})")
                skipped += 1
                continue

            inn1 = Innings.objects.filter(match=inn2.match, innings_number=1).first()
            if not inn1:
                self.stdout.write(f"SKIP Match {inn2.match.id} - no 1st innings found")
                skipped += 1
                continue

            inn2.target = inn1.total_runs + 1
            inn2.save()
            self.stdout.write(f"FIXED Match {inn2.match.id} ({inn2.match}) - target set to {inn2.target}")
            fixed += 1

        self.stdout.write(f"\nDone! {fixed} target(s) fixed, {skipped} skipped.")