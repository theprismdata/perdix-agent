"""PyInstaller용 진입점. Windows 프로세스 초기화 후 ACP 서버를 실행한다."""

import multiprocessing

from perdix.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
