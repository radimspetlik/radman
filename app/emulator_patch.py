# flake8: noqa
# pylint: skip-file
from azure.data.tables.aio._base_client_async import Any, List, Mapping
from azure.data.tables._base_client import (
    _decode_error, extract_batch_part_metadata, HttpRequest, RequestTooLargeError,
    StorageHeadersPolicy, TableTransactionError, uuid4)


def _batch_send(self, *reqs, **kwargs):
    # type: (List[HttpRequest], Any) -> List[Mapping[str, Any]]
    """Given a series of request, do a Storage batch call."""
    # Pop it here, so requests doesn't feel bad about additional kwarg
    policies = [StorageHeadersPolicy()]

    changeset = HttpRequest("POST", None)  # type: ignore
    changeset.set_multipart_mixed(
        *reqs, policies=policies, boundary="changeset_{}".format(uuid4())  # type: ignore
    )
    request = self._client._client.post(  # pylint: disable=protected-access
        url="http://{}/$batch".format(self._primary_hostname),
        headers={
            "x-ms-version": self.api_version,
            "DataServiceVersion": "3.0",
            "MaxDataServiceVersion": "3.0;NetFx",
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        },
    )
    request.set_multipart_mixed(
        changeset,
        policies=policies,
        enforce_https=False,
        boundary="batch_{}".format(uuid4()),
    )
    pipeline_response = self._client._client._pipeline.run(request,
                                                           **kwargs)  # pylint: disable=protected-access
    response = pipeline_response.http_response
    if response.status_code == 413:
        raise _decode_error(
            response,
            error_message="The transaction request was too large",
            error_type=RequestTooLargeError)
    if response.status_code != 202:
        raise _decode_error(response)

    parts = list(response.parts())
    error_parts = [p for p in parts if not 200 <= p.status_code < 300]
    if any(error_parts):
        if error_parts[0].status_code == 413:
            raise _decode_error(
                response,
                error_message="The transaction request was too large",
                error_type=RequestTooLargeError)
        raise _decode_error(
            response=error_parts[0],
            error_type=TableTransactionError
        )
    return [extract_batch_part_metadata(p) for p in parts]


async def _batch_send_async(self, *reqs: "HttpRequest", **kwargs) -> List[Mapping[str, Any]]:
    """Given a series of request, do a Storage batch call."""
    # Pop it here, so requests doesn't feel bad about additional kwarg
    policies = [StorageHeadersPolicy()]

    changeset = HttpRequest("POST", None)  # type: ignore
    changeset.set_multipart_mixed(
        *reqs, policies=policies, boundary="changeset_{}".format(uuid4())
    )
    request = self._client._client.post(  # pylint: disable=protected-access
        url="http://{}/$batch".format(self._primary_hostname),
        headers={
            "x-ms-version": self.api_version,
            "DataServiceVersion": "3.0",
            "MaxDataServiceVersion": "3.0;NetFx",
            "Content-Type": "application/json",
            "Accept": "application/json"
        },
    )
    request.set_multipart_mixed(
        changeset,
        policies=policies,
        enforce_https=False,
        boundary="batch_{}".format(uuid4()),
    )

    pipeline_response = await self._client._client._pipeline.run(request, **kwargs)  # pylint: disable=protected-access
    response = pipeline_response.http_response
    # TODO: Check for proper error model deserialization
    if response.status_code == 413:
        raise _decode_error(
            response,
            error_message="The transaction request was too large",
            error_type=RequestTooLargeError)
    if response.status_code != 202:
        raise _decode_error(response)

    parts_iter = response.parts()
    parts = []
    async for p in parts_iter:
        parts.append(p)
    error_parts = [p for p in parts if not 200 <= p.status_code < 300]
    if any(error_parts):
        if error_parts[0].status_code == 413:
            raise _decode_error(
                response,
                error_message="The transaction request was too large",
                error_type=RequestTooLargeError)
        raise _decode_error(
            response=error_parts[0],
            error_type=TableTransactionError,
        )
    return [extract_batch_part_metadata(p) for p in parts]
