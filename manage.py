#!/usr/bin/env python3
"""
Portal CLI - User Management Tool
"""
import sys
import os
import argparse
from getpass import getpass

# Add app to path
sys.path.insert(0, '.')

# Import only what we need (avoid loading main.py)
from app.models import init_db, get_session, User
from app.auth import hash_password, generate_totp_secret, get_totp_uri, verify_totp


def create_user(args):
    """Create a new user"""
    db = get_session()
    
    # Check if user exists
    existing = db.query(User).filter(User.username == args.username).first()
    if existing:
        print(f"Error: User '{args.username}' already exists")
        return 1
    
    # Get password
    if args.password:
        password = args.password
    else:
        password = getpass("Password: ")
        confirm = getpass("Confirm password: ")
        if password != confirm:
            print("Error: Passwords don't match")
            return 1
    
    user = User(
        username=args.username,
        email=args.email,
        hashed_password=hash_password(password),
        is_admin=args.admin,
        allowed_campuses=args.campuses or 'all'
    )
    db.add(user)
    db.commit()
    
    print(f"OK Created user '{args.username}'")
    if args.admin:
        print("   (admin)")
    
    return 0


def list_users(args):
    """List all users"""
    db = get_session()
    users = db.query(User).order_by(User.username).all()
    
    print(f"\n{'Username':<20} {'Email':<30} {'Admin':<6} {'2FA':<6} {'Active':<6}")
    print("-" * 80)
    
    for user in users:
        print(f"{user.username:<20} {user.email:<30} {'Yes' if user.is_admin else 'No':<6} "
              f"{'Yes' if user.totp_enabled else 'No':<6} {'Yes' if user.is_active else 'No':<6}")
    
    print(f"\nTotal: {len(users)} users")
    return 0


def reset_password(args):
    """Reset a user's password"""
    db = get_session()
    user = db.query(User).filter(User.username == args.username).first()
    
    if not user:
        print(f"Error: User '{args.username}' not found")
        return 1
    
    if args.password:
        password = args.password
    else:
        password = getpass("New password: ")
        confirm = getpass("Confirm password: ")
        if password != confirm:
            print("Error: Passwords don't match")
            return 1
    
    user.hashed_password = hash_password(password)
    db.commit()
    
    print(f"OK Password reset for '{args.username}'")
    return 0


def reset_2fa(args):
    """Reset a user's 2FA"""
    db = get_session()
    user = db.query(User).filter(User.username == args.username).first()
    
    if not user:
        print(f"Error: User '{args.username}' not found")
        return 1
    
    user.totp_secret = None
    user.totp_enabled = False
    db.commit()
    
    print(f"OK 2FA reset for '{args.username}'")
    print("   User will need to setup 2FA again on next login")
    return 0


def setup_2fa_cli(args):
    """Setup 2FA for a user via CLI"""
    db = get_session()
    user = db.query(User).filter(User.username == args.username).first()
    
    if not user:
        print(f"Error: User '{args.username}' not found")
        return 1
    
    if user.totp_enabled:
        print(f"2FA is already enabled for '{args.username}'")
        reset = input("Reset and create new secret? (y/n): ").strip().lower()
        if reset != 'y':
            return 0
    
    # Generate new secret
    secret = generate_totp_secret()
    user.totp_secret = secret
    db.commit()
    
    uri = get_totp_uri(user.username, secret)
    
    print(f"\n2FA Setup for '{args.username}'")
    print("=" * 50)
    print(f"\nSecret: {secret}")
    print(f"\nURI (for QR code): {uri}")
    print("\nEnter this secret in your authenticator app.")
    
    # Verify
    code = input("\nEnter code from app to verify: ").strip()
    if verify_totp(secret, code):
        user.totp_enabled = True
        db.commit()
        print("OK 2FA enabled successfully!")
        return 0
    else:
        print("Error: Invalid code. 2FA not enabled.")
        user.totp_secret = None
        db.commit()
        return 1


def delete_user(args):
    """Delete a user"""
    db = get_session()
    user = db.query(User).filter(User.username == args.username).first()
    
    if not user:
        print(f"Error: User '{args.username}' not found")
        return 1
    
    if not args.force:
        confirm = input(f"Delete user '{args.username}'? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Cancelled")
            return 0
    
    db.delete(user)
    db.commit()
    
    print(f"OK Deleted user '{args.username}'")
    return 0


def main():
    parser = argparse.ArgumentParser(description='Portal User Management')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Create user
    create_parser = subparsers.add_parser('create', help='Create a new user')
    create_parser.add_argument('username', help='Username')
    create_parser.add_argument('email', help='Email address')
    create_parser.add_argument('--password', '-p', help='Password (will prompt if not provided)')
    create_parser.add_argument('--admin', '-a', action='store_true', help='Make user an admin')
    create_parser.add_argument('--campuses', '-c', help='Allowed campus IDs (comma-separated) or "all"')
    
    # List users
    subparsers.add_parser('list', help='List all users')
    
    # Reset password
    reset_pw_parser = subparsers.add_parser('reset-password', help='Reset user password')
    reset_pw_parser.add_argument('username', help='Username')
    reset_pw_parser.add_argument('--password', '-p', help='New password')
    
    # Reset 2FA
    reset_2fa_parser = subparsers.add_parser('reset-2fa', help='Reset user 2FA')
    reset_2fa_parser.add_argument('username', help='Username')
    
    # Setup 2FA via CLI
    setup_2fa_parser = subparsers.add_parser('setup-2fa', help='Setup 2FA via CLI')
    setup_2fa_parser.add_argument('username', help='Username')
    
    # Delete user
    delete_parser = subparsers.add_parser('delete', help='Delete a user')
    delete_parser.add_argument('username', help='Username')
    delete_parser.add_argument('--force', '-f', action='store_true', help='Skip confirmation')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Initialize database
    init_db()
    
    commands = {
        'create': create_user,
        'list': list_users,
        'reset-password': reset_password,
        'reset-2fa': reset_2fa,
        'setup-2fa': setup_2fa_cli,
        'delete': delete_user,
    }
    
    return commands[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
