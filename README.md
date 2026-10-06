# BIC3TAB record editor

Flask application with two separate connections:

- Local MySQL authenticates users from the `users` table. There is no public registration or password recovery. Users with `isAdmin=1` can create standard user accounts from the application.
- IBM i reads and updates use the existing ODBC DSN and configured service credentials.

After a successful lookup, BICCPTCLI is displayed read-only and is never part of the IBM i `SET` clause. Successful IBM i updates are recorded in the MySQL `change_log` table with the original lookup values, saved values, the logged-in email, and a UTC timestamp.

## Database setup

Run the scripts in order using a MySQL account allowed to create databases and tables:

```sh
mysql -u root -p < sql/001_create_database.sql
mysql -u root -p < sql/002_create_tables.sql
```

The `password_hash` field stores Werkzeug password hashes, never plaintext passwords. Admins can create standard accounts from the **Adicionar utilizador** option in the application. The `isAdmin` field defaults to `0`, and accounts created in the application cannot grant themselves admin access. The CLI is also available for initial account setup:

```sh
python create_user.py
```

The CLI prompts for a name, email address, and password and hashes the password before insertion.

## Configure and run

Install Python dependencies and configure the environment. The defaults below match the supplied configuration; replace the MySQL URL, DSN, and credentials as appropriate.

```sh
export DATABASE_URL='mysql://root:@localhost/bic3tab'
export FLASK_SECRET_KEY='a-long-random-secret'
export AS400_DSN='cli000'
export AS400_UID='mobile'
export AS400_PWD='mobile'
export AS400_TABLE='OPERACOES.BIC3TAB'
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python run_waitress.py
```

For a Windows service, install a service wrapper such as NSSM and configure it to launch the virtual-environment Python executable with `run_waitress.py` as its argument. Set the service's working directory to this project folder. The runner loads a local `.env` file before importing the Flask app; copy `.env.example` to `.env`, fill in its values, and restrict access to that file to the service account. The Flask app honors the forwarded Nginx prefix automatically; set the Nginx `X-Forwarded-Prefix` header to `/bic3tab-flask-app`. NSSM's `Application` should be `.venv\Scripts\python.exe`, and its arguments should be `run_waitress.py`. Start it from an Administrator terminal with `nssm start BIC3TAB` after installing/configuring the service.

Waitress listens on `127.0.0.1:5800` by default. For access from other computers, set `BIC3TAB_HOST=0.0.0.0` in `.env`, allow TCP port 5800 in Windows Firewall for the required network profile, and open `http://<server-name-or-ip>:5800`. Configure `cli000` as a 64-bit System DSN, because a Windows service may run under an account that cannot see an interactive user's User DSN. To use Flask's development server instead, run `python app.py` (port 5800). `AS400_TABLE` may be a table name or a two-part `library.table` name. The app's ODBC host, port, database, and driver settings come from the existing DSN configuration. The machine running Flask needs a compatible IBM i ODBC driver and an installed DSN with access to the table.

For HTTPS behind a trusted reverse proxy, set `FLASK_BEHIND_HTTPS=1`. Do not expose Flask's development server directly to a network; use a production WSGI server and terminate TLS at a trusted proxy. Set a persistent, secret `FLASK_SECRET_KEY`, since signed form snapshots and sessions depend on it.

## Tables

`users`: `id`, `name`, `username` (unique email), `password_hash`, `isAdmin` (boolean admin flag, default `0`). For an existing database, add the field once if it is missing: `ALTER TABLE users ADD COLUMN isAdmin TINYINT(1) NOT NULL DEFAULT 0;`. The table creation script includes the field for new installations.

To grant admin access to an existing account, run `UPDATE users SET isAdmin = 1 WHERE username = 'admin@example.com';` and replace the email with that account's username.

`change_log`: `username`, `changed_at`, and the requested `_OLD` and `_NEW` columns for BICCPTCLI and its seven associated fields. `id` is an auto-incrementing audit-row key. BICVCLI is stored as `DECIMAL(5,0)`; character fields use their declared widths.

## Operational note

MySQL and IBM i are separate databases, so they cannot share one atomic transaction. The app commits the IBM i update first, then inserts the MySQL audit row. If MySQL fails after the IBM i commit, the page reports that the IBM i change succeeded but the audit insert failed; administrators should reconcile that case.
# bic3tab
