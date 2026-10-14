from pydantic import BaseModel

from haxdex.services.core.job_types import BaseResource


class ReverseLinesRequest(BaseModel, extra="forbid"):
    lines: list[str]


class ReverserResult(BaseModel, extra="forbid"):
    lines: list[str]


class FileReverserResource(BaseResource):
    resource_key = "file_reverser"

    def handle(self, request: ReverseLinesRequest) -> ReverserResult:
        return ReverserResult(lines=list(reversed(request.lines)))
