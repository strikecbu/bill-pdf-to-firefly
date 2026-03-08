#!/usr/bin/env python3
"""CLI tool for testing PDF parsing without running the full server."""

import argparse
import json
import os
import sys
import tempfile

import pikepdf
import pdfplumber

from app.config import load_config
from app.parsers import ParserFactory


def unlock_pdf(pdf_path: str, password: str) -> str:
    """Unlock PDF to a temp file, return path."""
    unlocked = tempfile.NamedTemporaryFile(suffix="_unlocked.pdf", delete=False)
    pdf = pikepdf.open(pdf_path, password=password)
    pdf.save(unlocked.name)
    pdf.close()
    return unlocked.name


def cmd_parse(args):
    """Parse a PDF statement and display transactions."""
    settings = load_config(args.config)
    pdf_path = args.pdf

    # Determine if we need to unlock
    parse_path = pdf_path
    temp_path = None
    try:
        pdfplumber.open(pdf_path).close()
    except Exception:
        # PDF is likely encrypted
        password = args.password
        if not password:
            bank_config = settings.banks.get(args.bank)
            if bank_config:
                user = settings.user
                password = bank_config.pdf_password_template.format(
                    id_number=user.id_number,
                    birthday=user.birthday,
                    phone=user.phone,
                )
        if not password:
            print("Error: PDF is encrypted. Provide --password or set user info in config.", file=sys.stderr)
            sys.exit(1)

        print(f"Unlocking PDF with password...")
        temp_path = unlock_pdf(pdf_path, password)
        parse_path = temp_path

    try:
        parser = ParserFactory.get_parser(args.bank)
        transactions = parser.parse(parse_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    if not transactions:
        print("No transactions found.")
        return

    if args.format == "json":
        print(json.dumps(transactions, ensure_ascii=False, indent=2))
        return

    # Table output
    print(f"\nFound {len(transactions)} transactions:\n")
    print(f"{'#':>3}  {'Date':10}  {'Type':10}  {'Amount':>10}  {'Source':20}  {'Destination':20}  {'Description'}")
    print("-" * 110)
    for i, t in enumerate(transactions, 1):
        print(
            f"{i:>3}  "
            f"{t.get('transaction_date', ''):10}  "
            f"{t.get('transaction_type', ''):10}  "
            f"{t.get('amount', 0):>10,.0f}  "
            f"{t.get('source_account', '')[:20]:20}  "
            f"{t.get('destination_account', '')[:20]:20}  "
            f"{t.get('description', '')}"
        )
    print()

    total = sum(t.get("amount", 0) for t in transactions if t.get("transaction_type") == "withdrawal")
    deposit = sum(t.get("amount", 0) for t in transactions if t.get("transaction_type") == "deposit")
    transfer = sum(t.get("amount", 0) for t in transactions if t.get("transaction_type") == "transfer")
    print(f"Summary: {len(transactions)} transactions | Withdrawal: {total:,.0f} | Deposit: {deposit:,.0f} | Transfer: {transfer:,.0f}")


def cmd_banks(args):
    """List configured banks."""
    settings = load_config(args.config)
    print(f"\n{'Code':10}  {'Name':10}  {'Parser':20}  {'Cards'}")
    print("-" * 60)
    for code, bank in settings.banks.items():
        cards = ", ".join(f"{k}:{v}" for k, v in bank.cards.items())
        print(f"{code:10}  {bank.name:10}  {bank.parser_class:20}  {cards}")


def cmd_raw(args):
    """Show raw PDF tables/text for debugging parser development."""
    pdf_path = args.pdf
    parse_path = pdf_path
    temp_path = None

    try:
        pdfplumber.open(pdf_path).close()
    except Exception:
        if not args.password:
            print("Error: PDF is encrypted. Provide --password.", file=sys.stderr)
            sys.exit(1)
        temp_path = unlock_pdf(pdf_path, args.password)
        parse_path = temp_path

    try:
        with pdfplumber.open(parse_path) as pdf:
            for i, page in enumerate(pdf.pages):
                print(f"\n{'='*60}")
                print(f"  PAGE {i + 1}")
                print(f"{'='*60}")

                if args.mode in ("text", "all"):
                    text = page.extract_text()
                    if text:
                        print(f"\n--- Text ---")
                        print(text)

                if args.mode in ("tables", "all"):
                    tables = page.extract_tables()
                    for j, table in enumerate(tables):
                        print(f"\n--- Table {j + 1} ({len(table)} rows) ---")
                        for row in table:
                            print(row)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def main():
    parser = argparse.ArgumentParser(
        description="Credit Card Statement CLI Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 cli.py parse statement.pdf --bank sinopac --password S123456789
  python3 cli.py parse statement.pdf --bank sinopac --format json
  python3 cli.py raw statement.pdf --password S123456789 --mode tables
  python3 cli.py banks
        """,
    )
    parser.add_argument("--config", default="config/config.yaml", help="Config file path")
    sub = parser.add_subparsers(dest="command", required=True)

    # parse command
    p_parse = sub.add_parser("parse", help="Parse a PDF statement")
    p_parse.add_argument("pdf", help="Path to PDF file")
    p_parse.add_argument("--bank", "-b", default="sinopac", help="Bank code (default: sinopac)")
    p_parse.add_argument("--password", "-p", help="PDF password (overrides config)")
    p_parse.add_argument("--format", "-f", choices=["table", "json"], default="table", help="Output format")
    p_parse.set_defaults(func=cmd_parse)

    # raw command
    p_raw = sub.add_parser("raw", help="Show raw PDF content (for debugging)")
    p_raw.add_argument("pdf", help="Path to PDF file")
    p_raw.add_argument("--password", "-p", help="PDF password")
    p_raw.add_argument("--mode", "-m", choices=["text", "tables", "all"], default="all", help="What to show")
    p_raw.set_defaults(func=cmd_raw)

    # banks command
    p_banks = sub.add_parser("banks", help="List configured banks")
    p_banks.set_defaults(func=cmd_banks)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
