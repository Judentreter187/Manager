# New-test

## Merge-Konflikte bereinigen

Falls in Dateien noch Marker wie `<<<<<<<` oder `>>>>>>>` auftauchen, kannst du sie lokal
mit dem Script bereinigen. Hinweis: In diesem Repo tauchen die Marker im Code nur als
Beispieltext auf (nicht als echte Konflikte). Wenn GitHub einen Merge-Konflikt meldet,
nutze das Script auf dem konfliktbehafteten Branch oder löse ihn direkt in der PR-Ansicht.

```bash
python scripts/resolve_merge_conflicts.py --apply --keep=ours
```
