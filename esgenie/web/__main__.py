"""Serve the built frontend and API on loopback, using the existing engine."""
import argparse


def main():
    parser = argparse.ArgumentParser(description="ESGenie guided workspace")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    import uvicorn
    from .app import create_app
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
