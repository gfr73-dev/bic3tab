import getpass

from werkzeug.security import generate_password_hash

from app import User, app, db


def main():
    name = input("Name: ").strip()
    username = input("Email/username: ").strip().lower()
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if not name or not username or not password:
        raise SystemExit("Name, email, and password are required.")
    if password != confirm:
        raise SystemExit("Passwords do not match.")
    with app.app_context():
        if User.query.filter_by(username=username).first():
            raise SystemExit("That email address already exists.")
        user = User(name=name, username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
    print(f"Added user {username}")


if __name__ == "__main__":
    main()
