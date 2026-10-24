# Data migrations

`app.py` calls `ensure_surveillance_schema()` during startup inside the normal
SQLite transaction. The migration adds `property_imagery_pairs`,
`change_markers`, and `surveillance_tasks` plus indexes. It uses
`CREATE TABLE IF NOT EXISTS`; the two optional mission-suggestion columns are
and the marker warning field are added only when absent. It does not drop,
rename, or rewrite existing rows.

`auth_store.ensure_auth_schema()` adds the `users` table. Accounts are created
with `create_first_admin.py`; the script prompts for a unique username and a
new password and refuses to create another first administrator when any user
already exists. Passwords are stored as Argon2id hashes.
An authenticated administrator can provision Viewer, Officer, or Admin
accounts with `manage_users.py`; passwords are set interactively and never
use a shared default.

To apply on a local Windows checkout after installing requirements:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe create_first_admin.py
.\.venv\Scripts\python.exe manage_users.py
.\.venv\Scripts\streamlit.exe run app.py
```

Back up `smartpanchayat.db` and `change_images/` before upgrading an existing
installation. Image pairs and markers are append-only; rerunning the same
comparison uses deterministic keys and does not create duplicate markers.
