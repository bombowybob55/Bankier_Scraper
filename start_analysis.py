#!/usr/bin/env python3
"""
start_analysis.py — Orkiestrator Swetrowo Market Brief
======================================================
Uruchamia wszystkie skrypty analizy w kolejności, następnie otwiera
stronę swetrowo.html w przeglądarce.

Ulepszenia v2:
  - Rich progress bar (jeśli zainstalowany)
  - Zapis logu do logs/run_YYYYMMDD_HHMMSS.log
  - Tryb --skip-failed: kontynuuj mimo błędu opcjonalnych skryptów
  - Tryb --dry-run: wyświetl listę bez uruchamiania
  - Pomiar czasu każdego skryptu
  - Sprawdzenie dostępności Internetu przed startem
"""

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path


def reexecute_in_venv():
    """
    Checks if a virtual environment (.venv or venv) exists in the script's directory.
    If yes, and we are not currently running using it, re-executes the script
    using the virtual environment's Python interpreter.
    """
    # Avoid infinite loop: check if we are already inside a virtualenv or already re-executed
    if os.environ.get("ALREADY_REEXECUTED") or os.environ.get("VIRTUAL_ENV"):
        return

    # Check for venv python path in BASE_DIR
    base_dir = Path(__file__).resolve().parent
    for venv_dir in [".venv", "venv"]:
        for rel_path in [Path("bin") / "python", Path("Scripts") / "python.exe"]:
            venv_python = base_dir / venv_dir / rel_path
            if venv_python.exists():
                # Avoid re-executing if sys.executable is already the same file
                try:
                    if Path(sys.executable).resolve() == venv_python.resolve():
                        return
                except Exception:
                    pass

                # Set environment flags to prevent loops and set virtualenv context
                env = os.environ.copy()
                env["ALREADY_REEXECUTED"] = "1"
                env["VIRTUAL_ENV"] = str(base_dir / venv_dir)
                
                try:
                    # Run the subprocess and exit with its code
                    result = subprocess.run([str(venv_python)] + sys.argv, env=env)
                    sys.exit(result.returncode)
                except Exception as e:
                    # If execution fails, print warning and continue with current interpreter
                    print(f"Warning: Failed to re-execute in venv ({venv_python}): {e}", file=sys.stderr)
                    return


# Run re-execution check immediately
reexecute_in_venv()


# Opcjonalne — Rich dla ładnego formatowania
try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table
    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    console = None  # type: ignore

# ============================================================================
# KONFIGURACJA
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"

# Skrypty do uruchomienia w kolejności
# Pole: (ścieżka, opcjonalny?)
#   opcjonalny=True → błąd nie przerywa sekwencji
SCRIPTS = [
    # Dane historyczne GPW & zagraniczne
    ("kursy_ostatnie.py",                       False),
    ("kursy_zagr_upd.py",                       False),
    # Dane makroekonomiczne (nowe)
    ("macro_data.py",                           True),
    # Fundamenty GPW (nowe — opcjonalne, API Yahoo Finance)
    ("fundaments_gpw.py",                       True),
    # Analiza techniczna
    ("Technical/stock_analysis_zagr.py",        False),
    ("Technical/stock_analysis.py",             False),
    # Sentyment Bankier
    ("Bankier_sentyment/Antigrav_sentiment.py", False),
    # Alerty techniczne (nowe)
    ("alerts.py",                               True),
    # Analiza łączona + generowanie strony
    ("combined_analysis.py",                    False),
    ("generate_swetrowo.py",                    False),
]

HTML_OUTPUT = "swetrowo.html"

# ============================================================================
# LOGGING
# ============================================================================

_log_file = None


def init_logger() -> Path:
    """Inicjalizuje plik logu i zwraca jego ścieżkę."""
    global _log_file
    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"run_{ts}.log"
    _log_file = log_path.open("w", encoding="utf-8")
    _log_file.write(f"=== Swetrowo Market Brief — run started at {ts} ===\n\n")
    return log_path


def log(msg: str):
    """Zapisuje do logu (i konsoli jeśli nie ma Rich)."""
    if _log_file:
        _log_file.write(msg + "\n")
        _log_file.flush()
    if not HAS_RICH:
        print(msg)


def close_logger():
    if _log_file:
        _log_file.write("\n=== run ended ===\n")
        _log_file.close()


# ============================================================================
# SPRAWDZENIE INTERNETU
# ============================================================================

def check_internet() -> bool:
    """Szybkie sprawdzenie połączenia z Internetem."""
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3.0)
            s.connect(("8.8.8.8", 53))
        return True
    except OSError:
        return False


# ============================================================================
# URUCHAMIANIE SKRYPTÓW
# ============================================================================

def run_script(script_path: str) -> tuple[bool, float, str]:
    """
    Uruchamia skrypt Pythona jako subprocess.
    Zwraca (sukces: bool, czas: float, wyjście: str).
    """
    full_path = BASE_DIR / script_path

    if not full_path.exists():
        msg = f"❌ Nie znaleziono pliku: {script_path}"
        log(msg)
        return False, 0.0, msg

    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, str(full_path)],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
        )
        duration = time.time() - start
        output = (result.stdout or "") + (result.stderr or "")

        log(f"\n--- {script_path} (exit={result.returncode}, {duration:.2f}s) ---")
        log(output[:4000])  # max 4000 znaków w logu

        if result.returncode == 0:
            return True, duration, output
        else:
            return False, duration, output

    except Exception as e:
        duration = time.time() - start
        msg = f"EXCEPTION: {e}"
        log(msg)
        return False, duration, msg


# ============================================================================
# DRY RUN
# ============================================================================

def dry_run():
    """Wyświetla listę skryptów bez uruchamiania."""
    if HAS_RICH:
        table = Table(title="Swetrowo Market Brief — Dry Run", show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", width=4)
        table.add_column("Skrypt", style="white")
        table.add_column("Opcjonalny", justify="center")
        for i, (script, optional) in enumerate(SCRIPTS, 1):
            table.add_row(str(i), script, "✓" if optional else "")
        console.print(table)
    else:
        print(f"\n{'='*55}")
        print("  DRY RUN — lista skryptów")
        print(f"{'='*55}")
        for i, (script, optional) in enumerate(SCRIPTS, 1):
            opt_tag = " [opcjonalny]" if optional else ""
            print(f"  {i:2}. {script}{opt_tag}")
        print(f"{'='*55}\n")


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Swetrowo Market Brief — Orkiestrator")
    parser.add_argument("--dry-run",      action="store_true", help="Wyświetl listę skryptów bez uruchamiania")
    parser.add_argument("--skip-failed",  action="store_true", help="Kontynuuj sekwencję mimo błędów we wszystkich skryptach")
    parser.add_argument("--no-browser",   action="store_true", help="Nie otwieraj przeglądarki po zakończeniu")
    parser.add_argument("--no-check-net", action="store_true", help="Pomiń sprawdzenie Internetu")
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return

    # --- Inicjalizacja ---
    log_path = init_logger()
    run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if HAS_RICH:
        console.rule("[bold yellow]Swetrowo Market Brief[/]")
        console.print(f"[dim]Start: {run_ts}  |  Log: {log_path}[/]")
        console.print(f"[dim]Katalog: {BASE_DIR}[/]\n")
    else:
        print(f"\n{'='*55}")
        print("  Swetrowo Market Brief — start")
        print(f"  {run_ts}")
        print(f"  Log: {log_path}")
        print(f"{'='*55}\n")

    # --- Sprawdzenie Internetu ---
    if not args.no_check_net:
        net_ok = check_internet()
        net_status = "✅ Połączenie OK" if net_ok else "⚠️  Brak Internetu (część danych może być niedostępna)"
        log(f"Internet: {net_status}")
        if HAS_RICH:
            color = "green" if net_ok else "yellow"
            console.print(f"[{color}]{net_status}[/]\n")
        else:
            print(net_status + "\n")

    # --- Uruchamianie skryptów ---
    total_start = time.time()
    results = []

    if HAS_RICH:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        )
        task_id = progress.add_task("[cyan]Uruchamianie skryptów...", total=len(SCRIPTS))

        with progress:
            for script_path, optional in SCRIPTS:
                name = Path(script_path).name
                progress.update(task_id, description=f"[cyan]▶ {name}")
                log(f"\n🚀 LAUNCHING: {script_path}")

                success, duration, output = run_script(script_path)
                results.append((script_path, optional, success, duration))

                progress.advance(task_id)

                if success:
                    progress.update(task_id, description=f"[green]✅ {name}  ({duration:.1f}s)")
                else:
                    if optional or args.skip_failed:
                        progress.update(task_id, description=f"[yellow]⚠️  {name} (opcjonalny, pominięty)")
                    else:
                        progress.update(task_id, description=f"[red]❌ {name} — BŁĄD")
                        progress.stop()
                        console.print(f"\n[bold red]⛔ STOP: {script_path} zakończony błędem.[/]")
                        console.print("[dim]Sprawdź log lub uruchom z --skip-failed[/]")
                        close_logger()
                        sys.exit(1)
    else:
        for script_path, optional in SCRIPTS:
            print(f"\n{'='*50}")
            print(f"🚀 LAUNCHING: {script_path}")
            print(f"{'='*50}")
            log(f"\n🚀 LAUNCHING: {script_path}")

            success, duration, output = run_script(script_path)
            results.append((script_path, optional, success, duration))

            if success:
                print(f"✅ OK  ({duration:.2f}s)")
            else:
                if optional or args.skip_failed:
                    print("⚠️  FAILED (opcjonalny — kontynuuję)")
                else:
                    print("❌ FAILED — STOP")
                    close_logger()
                    sys.exit(1)

    total_duration = time.time() - total_start

    # --- Podsumowanie ---
    if HAS_RICH:
        console.print()
        table = Table(title="Wyniki", show_header=True, header_style="bold white")
        table.add_column("Skrypt", style="white")
        table.add_column("Status", justify="center")
        table.add_column("Czas", justify="right", style="dim")
        for script_path, optional, success, duration in results:
            name = Path(script_path).name
            if success:
                status = "[green]✅ OK[/]"
            elif optional:
                status = "[yellow]⚠️  SKIP[/]"
            else:
                status = "[red]❌ FAIL[/]"
            table.add_row(name, status, f"{duration:.1f}s")
        console.print(table)
        console.print(f"\n[bold green]🎉 Ukończono w {total_duration:.1f}s[/]")
    else:
        print(f"\n{'='*50}")
        print(f"🎉 Ukończono w {total_duration:.2f}s")
        for script_path, optional, success, duration in results:
            icon = "✅" if success else ("⚠️" if optional else "❌")
            print(f"  {icon}  {script_path:<45} {duration:.1f}s")
        print(f"{'='*50}\n")

    log(f"\nŁączny czas: {total_duration:.2f}s")

    # --- Otwieranie przeglądarki ---
    if not args.no_browser:
        html_path = BASE_DIR / HTML_OUTPUT
        if html_path.exists():
            url = f"file://{html_path.resolve()}"
            log(f"Otwieranie przeglądarki: {url}")
            if HAS_RICH:
                console.print(f"[cyan]🌍 Otwieranie {HTML_OUTPUT}...[/]")
            else:
                print(f"🌍 Otwieranie {HTML_OUTPUT}...")
            try:
                webbrowser.open(url)
            except Exception as e:
                log(f"Błąd otwierania przeglądarki: {e}")
        else:
            msg = f"❌ Nie znaleziono pliku {HTML_OUTPUT}"
            log(msg)
            if HAS_RICH:
                console.print(f"[red]{msg}[/]")
            else:
                print(msg)

    close_logger()


if __name__ == "__main__":
    main()
