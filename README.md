# BIC3TAB record editor

Flask application with two separate connections:

- Local MySQL authenticates users from the `users` table. There is no public registration or password recovery.
- IBM i reads and updates use the existing ODBC DSN and configured service credentials.

After a successful lookup, BICCPTCLI is displayed read-only and is never part of the IBM i `SET` clause. Successful IBM i updates are recorded in the MySQL `change_log` table with the original lookup values, saved values, the logged-in email, and a UTC timestamp.

## Database setup

Run the scripts in order using a MySQL account allowed to create databases and tables:

```sh
mysql -u root -p < sql/001_create_database.sql
mysql -u root -p < sql/002_create_tables.sql
```

The `password_hash` field stores Werkzeug password hashes, never plaintext passwords. Add users through the admin CLI (there is no registration page):

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
python app.py
```

Open http://127.0.0.1:5000. `AS400_TABLE` may be a table name or a two-part `library.table` name. The app's ODBC host, port, database, and driver settings come from the existing DSN configuration. The machine running Flask needs a compatible IBM i ODBC driver and an installed DSN with access to the table.

For HTTPS behind a trusted reverse proxy, set `FLASK_BEHIND_HTTPS=1`. Do not expose Flask's development server directly to a network; use a production WSGI server and terminate TLS at a trusted proxy. Set a persistent, secret `FLASK_SECRET_KEY`, since signed form snapshots and sessions depend on it.

## Tables

`users`: `id`, `name`, `username` (unique email), `password_hash`.

`change_log`: `username`, `changed_at`, and the requested `_OLD` and `_NEW` columns for BICCPTCLI and its seven associated fields. `id` is an auto-incrementing audit-row key. BICVCLI is stored as `DECIMAL(5,0)`; character fields use their declared widths.

## Operational note

MySQL and IBM i are separate databases, so they cannot share one atomic transaction. The app commits the IBM i update first, then inserts the MySQL audit row. If MySQL fails after the IBM i commit, the page reports that the IBM i change succeeded but the audit insert failed; administrators should reconcile that case.
# bic3tab
