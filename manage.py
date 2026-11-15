"""Kezelő parancsok.

    python manage.py create_user <név>      # új felhasználó (jelszót bekéri)
    python manage.py demo                    # demó adatok a felület kipróbálásához
"""
import getpass
import sys

from werkzeug.security import generate_password_hash

from db import connect, init_db


def create_user(name):
    init_db()
    pw = getpass.getpass("Jelszó: ")
    if len(pw) < 8:
        sys.exit("A jelszó legalább 8 karakter legyen.")
    if pw != getpass.getpass("Jelszó újra: "):
        sys.exit("A két jelszó nem egyezik.")
    conn = connect()
    conn.execute(
        "INSERT INTO users(username, pw_hash) VALUES (?, ?) "
        "ON CONFLICT(username) DO UPDATE SET pw_hash = excluded.pw_hash",
        (name.strip().lower(), generate_password_hash(pw)),
    )
    conn.commit()
    print(f"Kész: {name}")


def demo():
    import demo_data
    demo_data.load()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "create_user" and len(sys.argv) == 3:
        create_user(sys.argv[2])
    elif cmd == "demo":
        demo()
    else:
        print(__doc__)
        sys.exit(1)
