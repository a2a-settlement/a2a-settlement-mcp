def main() -> int:
    try:
        import mcp  # noqa: F401
    except Exception as e:
        print("mcp import failed:", e)
        return 1
    print("mcp import: OK")

    try:
        import a2a_settlement_mcp  # noqa: F401
    except Exception as e:
        print("a2a_settlement_mcp import failed:", e)
        return 1
    print("a2a_settlement_mcp import: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

