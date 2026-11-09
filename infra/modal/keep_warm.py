"""Keep the chat endpoint warm while iterating, then release it.

Usage (needs the Modal CLI logged in):
    python infra/modal/keep_warm.py on     # keep one chat container running, so there is no cold start
    python infra/modal/keep_warm.py off    # back to scale-to-zero (stops the continuous GPU cost)

This overrides the autoscaler of the deployed function only. The next `modal deploy` resets it to the values in
vllm_app.py, so run `off` when you are done and do not rely on it surviving a redeploy.
"""

import sys

import modal

APP_NAME = "sre-agent-vllm"
FUNCTION_NAME = "chat"
USAGE = "usage: python infra/modal/keep_warm.py on|off"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"on", "off"}:
        print(USAGE, file=sys.stderr)
        return 2
    keep = argv[1] == "on"
    function = modal.Function.from_name(APP_NAME, FUNCTION_NAME)
    settings = function.update_autoscaler(min_containers=1 if keep else 0)
    state = "kept warm (billing continues until you run `off`)" if keep else "released to scale to zero"
    print(f"{APP_NAME}.{FUNCTION_NAME} {state}: {settings}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
