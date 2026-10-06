"""Write-back of ``troupe config set``: plan byte-surgical edits, verify, write, record.

Modules import only to the left in this chain (never ``troupe.cli``): ``value`` -> ``compare`` ->
``layout`` -> ``edits`` -> ``scalar_render`` -> ``block_scalar`` -> ``collection_render`` ->
``target`` -> ``shared`` -> ``aliases`` -> ``expand`` -> ``planner`` -> ``verify`` ->
``leaf_diff`` -> ``atomic`` -> ``setter``. Everything before ``atomic`` is pure; ``atomic`` is the
only code in the system that writes a user file, and no module here reads user files from disk.
"""
