#!/usr/local/autopkg/python
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Wrapper module that provides a consistent xattr interface
regardless of platform support.
"""
from typing import Any, List, Optional

from autopkglib import is_mac, is_windows

__all__ = ["getxattr", "listxattr", "removexattr", "setxattr"]

# Added for Windows version.
if is_windows():
    from autopkglib.pyads import pyads

    def getxattr(path: str, attr: str, symlink: bool = False) -> Optional[str]:
        handler = pyads.ADS(path)
        if handler.has_streams() and attr in handler.init_streams():
            return handler.get_stream_content(attr)
        return None

    def listxattr(path: str, symlink: bool = False) -> List[str]:
        handler = pyads.ADS(path)
        return handler.init_streams()

    def removexattr(path: str, attr: str, symlink: bool = False) -> None:
        handler = pyads.ADS(path)
        if handler.has_streams() and attr in handler.init_streams():
            return handler.delete_stream(attr)
        return None

    def setxattr(
        path: str, attr: str, value: str, options: int = 0, symlink: bool = False
    ) -> None:
        handler = pyads.ADS(path)
        return handler.add_stream_from_string(attr, value)


# End of Windows part.
else:

    class __xattr_wrapper:
        def __init__(self, impl: Any) -> None:
            self._impl = impl

        def getxattr(self, path: str, attr: str, symlink: bool = False) -> str:
            return self._impl.getxattr(path, attr, symlink)

        def listxattr(self, path: str, symlink: bool = False) -> List[str]:
            return self._impl.listxattr(path, symlink)

        def removexattr(self, path: str, attr: str, symlink: bool = False) -> None:
            return self._impl.removexattr(path, attr, symlink)

        def setxattr(
            self,
            path: str,
            attr: str,
            value: str,
            options: int = 0,
            symlink: bool = False,
        ) -> None:
            return self._impl.setxattr(path, attr, value, options, symlink)

    _xattr = __xattr_wrapper(None)

    try:
        import xattr as _xattr_real  # type: ignore

        _xattr = __xattr_wrapper(_xattr_real)
    except ImportError:
        print("WARNING: Library 'xattr' unavailable. Defining no-op implementation.")

        class __xattr_stub:
            """A stub class that will perform noop for any calls to the
            xattr module on platforms where it is not supported."""

            @staticmethod
            def getxattr(
                cls, path: str, attr: str, symlink: bool = False
            ) -> Optional[str]:
                return None

            @staticmethod
            def listxattr(cls, path: str, symlink: bool = False) -> List[str]:
                return []

            @staticmethod
            def removexattr(cls, path: str, attr: str, symlink: bool = False) -> None:
                return None

            @staticmethod
            def setxattr(
                cls,
                path: str,
                attr: str,
                value: str,
                options: int = 0,
                symlink: bool = False,
            ) -> None:
                return None

        _xattr = __xattr_wrapper(__xattr_stub)

    assert (
        _xattr._impl is not None
    ), "Failed to initialize xattr library, or stub. This is a bug."

    def getxattr(path: str, attr: str, symlink: bool = False) -> Optional[str]:
        return _xattr.getxattr(path, attr, symlink)

    def listxattr(path: str, symlink: bool = False) -> List[str]:
        return _xattr.listxattr(path, symlink)

    def removexattr(path: str, attr: str, symlink: bool = False) -> None:
        return _xattr.removexattr(path, attr, symlink)

    def setxattr(
        path: str, attr: str, value: str, options: int = 0, symlink: bool = False
    ) -> None:
        return _xattr.setxattr(path, attr, value, options, symlink)
