"""Script build embedding cho toàn bộ tin đăng.

CÁCH DÙNG:
    python -m scripts.build_embeddings
    python -m scripts.build_embeddings --force
"""

from __future__ import annotations

import argparse

from app.embeddings.sync_job import sync_embeddings


def main() -> None:
    parser = argparse.ArgumentParser(description="Build embedding cho tin đăng")
    parser.add_argument(
        "--force", action="store_true", help="Embed lại tất cả, không so hash"
    )
    args = parser.parse_args()

    run = sync_embeddings(force=args.force)
    print(
        f"Trạng thái: {run.status} | quét {run.scanned} | mới {run.embedded} | "
        f"không đổi {run.unchanged} | gỡ {run.removed}"
    )
    if run.error:
        print(f"Lỗi: {run.error}")
    if run.status != "SUCCESS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
