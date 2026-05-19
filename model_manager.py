"""Model Download Manager entry point — delegates to linguataxi.models.manager."""

from linguataxi.models.manager import (  # noqa: F401
    run_plan,
    check_updates,
    stamp_installed_models,
    main,
)

if __name__ == "__main__":
    main()
