"""fp connect — manage optional integrations.

Discover, install, and remove connectors
without needing to know about pip extras.
"""

import os
import sys
from pathlib import Path

from footprinter.cli._common import FORMATTER, add_json_flag, console, output_json
from footprinter.cli._prompt import PromptCancelled
from footprinter.cli._prompt import SafeConfirm as Confirm
from footprinter.connectors import (
    ConnectorSpec,
    discover_connectors,
    get_status,
    is_configured,
    is_installed,
    resolve_check_auth,
    resolve_hook,
)
from footprinter.connectors.config_utils import account_label

# ---------------------------------------------------------------------------
# argparse registration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Register the ``connect`` subcommand and its verbs."""
    parser = subparsers.add_parser(
        "connect",
        help="Manage optional integrations",
        description=(
            "Discover, install, and remove data source connectors.\n\n"
            "Connectors add support for external data sources.\n"
            "Each connector installs its own dependencies and runs\n"
            "a setup wizard."
        ),
        epilog=(
            "examples:\n"
            "  fp connect list                Show all connectors with status\n"
            "  fp connect install <name>      Install a connector\n"
            "  fp connect config <name>       Reconfigure a connector\n"
            "  fp connect status <name>       Detailed state and credentials\n"
            "  fp connect remove <name>       Uninstall connector packages\n"
            "\n"
            "tip: use 'fp connect <command> --help' for details on any command."
        ),
        formatter_class=FORMATTER,
    )

    def _connect_base(args, _parser=parser):
        if not discover_connectors():
            console.print(
                "Connectors add support for external data sources.\n"
                "Each connector is a separate package.\n\n"
                "No connectors are installed.\n\n"
                "Learn more: https://github.com/swellcitygroup/footprinter"
            )
            return
        _parser.print_help()

    parser.set_defaults(func=_connect_base)

    subs = parser.add_subparsers(dest="verb", metavar="COMMAND", title="commands (one required)")

    # list
    p_list = subs.add_parser(
        "list",
        help="Show all connectors with status",
        description="Show all available connectors with installed/configured status.",
        formatter_class=FORMATTER,
    )
    add_json_flag(p_list)
    p_list.set_defaults(func=_cmd_list)

    # install
    p_install = subs.add_parser(
        "install",
        help="Install a connector",
        description=(
            "Install a connector's dependencies and run its setup wizard.\n\n"
            "Installs pip extras and configures OAuth credentials."
        ),
        epilog=("examples:\n  fp connect install <name>    Install the <name> connector"),
        formatter_class=FORMATTER,
    )
    p_install.add_argument("name", nargs="?", default=None, help="Connector name (from fp connect list)")
    p_install.set_defaults(func=_cmd_install)

    # remove
    p_remove = subs.add_parser(
        "remove",
        help="Remove a connector",
        description="Uninstall a connector's packages and disable in config.",
        epilog=("examples:\n  fp connect remove <name>     Remove a connector and its packages"),
        formatter_class=FORMATTER,
    )
    p_remove.add_argument("name", nargs="?", default=None, help="Connector name (from fp connect list)")
    p_remove.set_defaults(func=_cmd_remove)

    # status
    p_status = subs.add_parser(
        "status",
        help="Show detailed connector state",
        description=(
            "Show detailed status for one or all connectors.\n\n"
            "Includes install state, config, credentials, and account details."
        ),
        epilog=(
            "examples:\n  fp connect status            All connectors\n  fp connect status <name>     Single connector"
        ),
        formatter_class=FORMATTER,
    )
    p_status.add_argument("name", nargs="?", default=None, help="Connector name (omit for all)")
    add_json_flag(p_status)
    p_status.set_defaults(func=_cmd_status)

    # config
    p_config = subs.add_parser(
        "config",
        help="Reconfigure an installed connector",
        description=(
            "Reconfigure an installed connector's settings.\n\n"
            "Opens the setup wizard in reconfiguration mode —\n"
            "skips dependency install, shows existing accounts,\n"
            "and allows adding or modifying accounts."
        ),
        epilog=("examples:\n  fp connect config <name>     Reconfigure a connector"),
        formatter_class=FORMATTER,
    )
    p_config.add_argument("name", nargs="?", default=None, help="Connector name (from fp connect list)")
    p_config.set_defaults(func=_cmd_config)

    # label
    p_label = subs.add_parser(
        "label",
        help="Set a display label for a connector account",
        description=(
            "Set a user-facing display label for a connector account.\n\n"
            "The internal account name stays unchanged — only the\n"
            "label shown in CLI output and status displays changes."
        ),
        epilog=("examples:\n  fp connect label <name> work Consulting\n  fp connect label <name> personal Family"),
        formatter_class=FORMATTER,
    )
    p_label.add_argument("name", nargs="?", default=None, help="Connector name (from fp connect list)")
    p_label.add_argument("account", nargs="?", default=None, help="Account name (e.g. work, personal)")
    p_label.add_argument("label", nargs="?", default=None, help="New display label")

    def _label_handler(args, _parser=p_label) -> None:
        if args.name is None or args.account is None or args.label is None:
            connectors = discover_connectors()
            if connectors:
                console.print("Available connectors:\n")
                for spec in connectors.values():
                    console.print(f"  [bold]{spec.name}[/bold] — {spec.description}")
                console.print("\nUsage: [bold]fp connect label <name> <account> <label>[/bold]")
            else:
                _parser.print_help()
            return
        _cmd_label(args)

    p_label.set_defaults(func=_label_handler)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _cmd_list(args) -> None:
    """Show all connectors with installed/configured status."""
    from footprinter.source_registry import ConfigError, get_config

    try:
        config = get_config()
    except ConfigError:
        config = {}

    connectors = discover_connectors()

    if not connectors:
        if getattr(args, "json", False):
            output_json([])
            return
        console.print(
            "No connectors installed.\n\n"
            "Connectors are separate packages that plug into Footprinter's\n"
            "ingest pipeline. To get started, visit:\n"
            "  https://github.com/swellcitygroup/footprinter"
        )
        return

    if getattr(args, "json", False):
        rows = []
        for spec in connectors.values():
            rows.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "status": get_status(spec, config),
                }
            )
        output_json(rows)
        return

    from rich.table import Table

    _STATUS_STYLE = {
        "installed": "[green]installed[/green]",
        "available": "[yellow]available[/yellow]",
        "not available": "[dim]not available[/dim]",
    }

    table = Table(title="Connectors")
    table.add_column("Name", style="bold")
    table.add_column("Description")
    table.add_column("Status")

    for spec in connectors.values():
        status = get_status(spec, config)
        table.add_row(spec.name, spec.description, _STATUS_STYLE.get(status, status))

    console.print(table)


def _cmd_install(args) -> None:
    """Install a connector's dependencies and run its setup hook."""
    import subprocess

    name = args.name
    connectors = discover_connectors()
    if name is None:
        if connectors:
            console.print("Available connectors:\n")
            for spec in connectors.values():
                console.print(f"  [bold]{spec.name}[/bold] — {spec.description}")
            console.print("\nInstall one: [bold]fp connect install <name>[/bold]")
        else:
            console.print("No connectors available.\n\nLearn more: https://github.com/swellcitygroup/footprinter")
        return
    spec = connectors.get(name)
    if spec is None:
        console.print(f"[red]Unknown connector:[/red] {name}")
        console.print(f"Available: {', '.join(connectors)}")
        sys.exit(1)

    already = is_installed(spec)
    if already:
        console.print(f"[green]{name}[/green] is already installed.")

        # Check if already configured — prompt before reconfiguring
        configured = is_configured(spec, _load_config())
        if configured:
            try:
                if not Confirm.ask(
                    f"  [bold]{name}[/bold] is already configured. Reconfigure?",
                    default=False,
                ):
                    console.print("  [dim]Keeping current configuration.[/dim]")
                    return
            except PromptCancelled:
                console.print("\n  [dim]Keeping current configuration.[/dim]")
                return
    else:
        console.print(f"Installing [bold]{name}[/bold] dependencies...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", f"footprinter-cli[{spec.extra}]"],
            )
        except (subprocess.CalledProcessError, OSError) as e:
            console.print(f"[red]Install failed:[/red] {e}")
            sys.exit(1)

        if not is_installed(spec):
            console.print(f"[red]Install failed:[/red] {spec.probe_module} not importable after pip install")
            sys.exit(1)

        console.print(f"[green]{name} dependencies installed.[/green]")

    # Run setup hook
    try:
        result = _resolve_setup_hook(spec, reconfigure=already)
        if result:
            _update_config_enabled(result, spec)
    except (
        Exception
    ) as e:  # Intentional broad catch: setup hook loading is unpredictable (dynamic import + user config)
        console.print(f"[red]Setup failed:[/red] {e}")
        sys.exit(1)


def _cmd_config(args) -> None:
    """Reconfigure an installed connector's settings."""
    name = args.name
    connectors = discover_connectors()
    if name is None:
        installed = {n: s for n, s in connectors.items() if is_installed(s)}
        if installed:
            console.print("Installed connectors:\n")
            for spec in installed.values():
                console.print(f"  [bold]{spec.name}[/bold] — {spec.description}")
            console.print("\nConfigure one: [bold]fp connect config <name>[/bold]")
        else:
            console.print("No connectors are installed.\n\nInstall one first: [bold]fp connect install <name>[/bold]")
        return
    spec = connectors.get(name)
    if spec is None:
        console.print(f"[red]Unknown connector:[/red] {name}")
        console.print(f"Available: {', '.join(connectors)}")
        sys.exit(1)

    if not is_installed(spec):
        console.print(f"[dim]{name} is not installed.[/dim]")
        console.print(f"  Install first: [bold]fp connect install {name}[/bold]")
        sys.exit(1)

    try:
        result = _resolve_setup_hook(spec, reconfigure=True)
        if result:
            _update_config_enabled(result, spec)
    except (
        Exception
    ) as e:  # Intentional broad catch: setup hook loading is unpredictable (dynamic import + user config)
        console.print(f"[red]Setup failed:[/red] {e}")
        sys.exit(1)


def _resolve_setup_hook(spec, **kwargs):
    """Resolve and call the connector's setup hook. Returns hook result."""
    hook_fn = resolve_hook(spec.setup_hook)
    if hook_fn is None:
        return None
    return hook_fn(**kwargs)


def _update_config_enabled(result: dict, spec: ConnectorSpec) -> None:
    """Update config enabled flags based on setup hook result.

    Args:
        result: Dict like {"personal": {"services": ["drive", "gmail"], "root_folder_id": "..."}}.
        spec: The ConnectorSpec whose config_apply hook to call.
    """
    from footprinter.cli.setup import _require_config, write_config

    config, config_path = _require_config()
    if spec.config_apply:
        fn = resolve_hook(spec.config_apply)
        if fn:
            fn(config, result)
    write_config(config, config_path)


def _cmd_remove(args) -> None:
    """Remove a connector's dependencies and disable in config."""
    import subprocess

    name = args.name
    connectors = discover_connectors()
    if name is None:
        installed = {n: s for n, s in connectors.items() if is_installed(s)}
        if installed:
            console.print("Installed connectors:\n")
            for spec in installed.values():
                console.print(f"  [bold]{spec.name}[/bold] — {spec.description}")
            console.print("\nRemove one: [bold]fp connect remove <name>[/bold]")
        else:
            console.print("No connectors are installed.")
        return
    spec = connectors.get(name)
    if spec is None:
        console.print(f"[red]Unknown connector:[/red] {name}")
        console.print(f"Available: {', '.join(connectors)}")
        sys.exit(1)

    if not is_installed(spec):
        console.print(f"[dim]{name} is not installed.[/dim]")
        return

    console.print(f"Removing [bold]{name}[/bold] packages...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "uninstall", "-y", *spec.remove_packages],
        )
    except (subprocess.CalledProcessError, OSError) as e:
        console.print(f"[red]Uninstall failed:[/red] {e}")
        sys.exit(1)

    _disable_config_sections(spec)
    console.print(f"[green]{name} removed.[/green]")


def _disable_config_sections(spec) -> None:
    """Disable config sections for a connector."""
    from footprinter.cli.setup import _require_config, write_config

    try:
        config, config_path = _require_config()
    except SystemExit:
        return

    for section in spec.config_sections:
        if section in config:
            config[section]["enabled"] = False
    write_config(config, config_path)


def _cmd_status(args) -> None:
    """Show detailed connector state."""
    name = getattr(args, "name", None)
    config = _load_config()
    connectors = discover_connectors()

    if name:
        spec = connectors.get(name)
        if spec is None:
            console.print(f"[red]Unknown connector:[/red] {name}")
            sys.exit(1)
        specs = [spec]
    else:
        specs = list(connectors.values())

    if getattr(args, "json", False):
        if len(specs) == 1:
            output_json(_status_dict(specs[0], config))
        else:
            output_json([_status_dict(s, config) for s in specs])
        return

    for spec in specs:
        _print_status_panel(spec, config)


def _load_config() -> dict:
    """Load config, returning empty dict on failure."""
    from footprinter.source_registry import ConfigError, get_config

    try:
        return get_config()
    except ConfigError:
        return {}


def _resolve_auth_label(spec: ConnectorSpec, config: dict) -> str:
    """Resolve auth status: check_auth callable if available, else credential existence."""
    from footprinter.connectors import has_credentials

    auth_result = resolve_check_auth(spec, config)
    if auth_result is not None:
        return auth_result
    return "credentials found" if has_credentials(spec, config) else "no credentials"


def _status_dict(spec: ConnectorSpec, config: dict) -> dict:
    """Build a status dict for a connector."""
    from footprinter.connectors import has_credentials as _has_creds

    creds = _has_creds(spec, config)
    auth = _resolve_auth_label(spec, config)

    accounts = []
    if is_configured(spec, config):
        for section in spec.config_sections:
            if section in config:
                for acct in config[section].get("accounts", []):
                    token_path = acct.get("token_path", "")
                    expanded = Path(os.path.expanduser(token_path))
                    accounts.append(
                        {
                            "name": acct.get("name", ""),
                            "label": account_label(acct),
                            "section": section,
                            "token_path": str(expanded),
                            "token_exists": expanded.exists(),
                        }
                    )

    return {
        "name": spec.name,
        "description": spec.description,
        "status": get_status(spec, config),
        "auth": auth,
        "credentials": creds,
        "pipes": list(spec.pipes),
        "accounts": accounts,
    }


def _print_status_panel(spec: ConnectorSpec, config: dict) -> None:
    """Print a Rich panel with connector status."""
    from rich.panel import Panel

    status = get_status(spec, config)
    status_style = {
        "installed": "[green]installed[/green]",
        "available": "[yellow]available[/yellow]",
        "not available": "[red]not available[/red]",
    }

    auth_style = {
        "authenticated": "[green]authenticated[/green]",
        "expired": "[yellow]expired[/yellow]",
        "error": "[red]error[/red]",
        "no credentials": "[red]no credentials[/red]",
        "credentials found": "[dim]credentials found[/dim]",
    }

    auth = _resolve_auth_label(spec, config)

    lines = []
    lines.append(f"  Status:     {status_style.get(status, status)}")
    lines.append(f"  Auth:       {auth_style.get(auth, auth)}")
    lines.append(f"  Pipes:      {', '.join(spec.pipes)}")

    # Account details — only when connector is configured
    if is_configured(spec, config):
        for section in spec.config_sections:
            if section in config:
                for acct in config[section].get("accounts", []):
                    token_path = Path(os.path.expanduser(acct.get("token_path", "")))
                    token_icon = "[green]yes[/green]" if token_path.exists() else "[red]no[/red]"
                    lines.append(f"  {account_label(acct)} ({section}): token {token_icon}")

    if status == "not available":
        lines.append(f"\n  Install: [bold]fp connect install {spec.name}[/bold]")
    elif status == "available":
        lines.append(f"\n  Configure: [bold]fp connect install {spec.name}[/bold]")

    console.print(
        Panel(
            "\n".join(lines),
            title=f"[bold]{spec.name}[/bold] — {spec.description}",
        )
    )


def _require_config_for_label() -> tuple[dict, str]:
    """Load config for the label command. Separate function for testability."""
    from footprinter.cli.setup import _require_config

    return _require_config()


def _cmd_label(args) -> None:
    """Set a display label for a connector account."""
    from footprinter.cli.setup import write_config

    name = args.name
    connectors = discover_connectors()
    spec = connectors.get(name)
    if spec is None:
        console.print(f"[red]Unknown connector:[/red] {name}")
        console.print(f"Available: {', '.join(connectors)}")
        sys.exit(1)

    config, config_path = _require_config_for_label()

    acct_name = args.account
    new_label = args.label
    found = False

    # Update account entries across all config sections for this connector
    for section in spec.config_sections:
        if section not in config:
            continue
        for acct in config[section].get("accounts", []):
            if acct.get("name") == acct_name:
                acct["label"] = new_label
                found = True

    if not found:
        console.print(f"[red]Account not found:[/red] {acct_name}")
        console.print(f"  Run [bold]fp connect status {name}[/bold] to see configured accounts.")
        sys.exit(1)

    # Update matching source seed labels (scoped to this connector's seeds)
    if spec.seed_prefix:
        seed_name = f"{spec.seed_prefix}_{acct_name}"
        for seed in config.get("source_seeds", []):
            if seed.get("name") == seed_name and seed.get("source_type") == "remote":
                if spec.seed_label_fn:
                    fn = resolve_hook(spec.seed_label_fn)
                    seed["label"] = fn(new_label) if fn else new_label
                else:
                    seed["label"] = new_label

    write_config(config, config_path)
    console.print(f"[green]Label updated:[/green] {acct_name} → {new_label}")
