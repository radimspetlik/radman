import datetime
import logging
import os

from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas
from azure.storage.blob._shared.request_handlers import get_length
from azure.storage.blob.aio import BlobServiceClient as BlobServiceClientAsync

import abc

from app.azure_constants import AzureConstants


class IBlobManager(abc.ABC):

    @abc.abstractmethod
    def blob_exists(self, blob_name):
        pass

    @abc.abstractmethod
    def download_blob(self, blob_name, timeout, etag):
        pass

    @abc.abstractmethod
    async def download_blob_async(self, blob_name, timeout, etag):
        pass

    @abc.abstractmethod
    def upload_blob(self, blob_name, data, timeout):
        pass

    @abc.abstractmethod
    async def upload_blob_async(self, blob_name, data, timeout):
        pass

    @abc.abstractmethod
    def generate_blob_url(self, blob_name):
        pass

    @abc.abstractmethod
    def create_container(self):
        pass

    @abc.abstractmethod
    async def create_container_async(self):
        pass

    @abc.abstractmethod
    def delete_containers(self):
        pass

    @abc.abstractmethod
    def delete_container(self, container_name):
        pass


class BlobManager(IBlobManager):

    def __init__(self, blob_connection_string, blob_container_name):
        self._blob_connection_string = blob_connection_string
        self._blob_container_name = blob_container_name

        self._logger = logging.getLogger("PyCloud")

    def blob_exists(self, blob_name):
        """
        Check if blob exists.
        :param blob_name: (str)
        :return: (bool)
        """
        self._logger.info("Checking if blob [%s] in container [%s] exists...",
                          blob_name, self._blob_container_name)
        blob_service_client, blob_client = self._get_sync_clients(blob_name)

        with blob_service_client:
            with blob_client:
                try:
                    return blob_client.exists()
                except (HttpResponseError, ResourceNotFoundError):
                    return False

    def download_blob(self, blob_name, timeout=90, etag=None):
        """
        Download the complete blob to memory.
        :param blob_name: (str)
        :param timeout: (int)
        :param etag: (str)
        :return: (tuple of (bytes, str))
        """
        self._logger.info("Downloading blob [%s] from container [%s].",
                          blob_name, self._blob_container_name)
        blob_service_client, blob_client = self._get_sync_clients(blob_name)
        latest_etag = None

        with blob_service_client:
            with blob_client:
                try:
                    if etag:
                        latest_etag = blob_client.get_blob_properties(timeout=10).etag
                        if latest_etag == etag:
                            self._logger.info("Blob ETag is the same: [%s].", latest_etag)
                            return None, latest_etag
                    blob_data = blob_client.download_blob(timeout=timeout).readall()
                    self._logger.info("Blob successfully downloaded.")
                    return blob_data, latest_etag
                except ResourceNotFoundError:
                    self._logger.warning("Blob not found.")
                    return None, None

    async def download_blob_async(self, blob_name, timeout=90, etag=None):
        """
        Download the complete blob to memory (async).
        :param blob_name: (str)
        :param timeout: (int)
        :param etag: (str)
        :return: (bytes)
        """
        self._logger.info("Downloading blob [%s] from container [%s].",
                          blob_name, self._blob_container_name)
        blob_service_client_async, blob_client_async = self._get_async_clients(blob_name)
        latest_etag = None

        async with blob_service_client_async:
            async with blob_client_async:
                try:
                    if etag:
                        latest_etag = (await blob_client_async.get_blob_properties(timeout=10)).etag
                        if latest_etag == etag:
                            self._logger.info("Blob ETag is the same: [%s].", latest_etag)
                            return None, latest_etag
                    downloaded_blob = await blob_client_async.download_blob(timeout=timeout)
                    blob_data = await downloaded_blob.readall()
                    self._logger.info("Blob successfully downloaded.")
                    return blob_data, latest_etag
                except ResourceNotFoundError:
                    self._logger.warning("Blob not found.")
                    return None, None

    def upload_blob(self, blob_name, data, timeout=90):
        """
        Upload binary data to blob.
        :param blob_name: (str)
        :param data: (bytes)
        :param timeout: (int)
        """
        self._logger.info(
            "Uploading data with size [%s] bytes to blob [%s].", get_length(data), blob_name)
        blob_service_client, blob_client = self._get_sync_clients(blob_name)

        with blob_service_client:
            with blob_client:
                blob_client.upload_blob(data, overwrite=True, timeout=timeout)

        self._logger.info("Blob successfully uploaded.")

    async def upload_blob_async(self, blob_name, data, timeout=90):
        """
        Upload binary data to blob (async).
        :param blob_name: (str)
        :param data: (bytes)
        :param timeout: (int)
        """
        self._logger.info(
            "Uploading data with size [%s] bytes to blob [%s].", get_length(data), blob_name)
        blob_service_client_async, blob_client_async = self._get_async_clients(blob_name)

        async with blob_service_client_async:
            async with blob_client_async:
                await blob_client_async.upload_blob(data, overwrite=True, timeout=timeout)
        self._logger.info("Blob successfully uploaded.")

    def generate_blob_url(self, blob_name):
        """
        Generate temporary url for blob with read-only access.
        :param blob_name: (str)
        :return: (str)
        """
        account_name = self._blob_connection_string.split("AccountName=")[1].split(";")[0]
        account_key = self._blob_connection_string.split("AccountKey=")[1].split(";")[0]

        # Azure: <http|https>://<account-name>.<service-name>.core.windows.net/<resource-path>
        # ASE: http://<local-machine-address>:<port>/<account-name>/<resource-path>
        if self._blob_connection_string == AzureConstants.AZURE_STORAGE_EMULATOR_CONNECTION_STRING:
            url_base = "http://127.0.0.1:10000/{0}/{1}/{2}?{3}"
        else:
            url_base = "https://{0}.blob.core.windows.net/{1}/{2}?{3}"

        sas_token = generate_blob_sas(
            account_name=account_name, account_key=account_key,
            container_name=self._blob_container_name, blob_name=blob_name,
            permission=BlobSasPermissions(read=True, tag=False),
            start=datetime.datetime.utcnow(),
            expiry=datetime.datetime.utcnow() + datetime.timedelta(hours=1))

        url = url_base.format(account_name, self._blob_container_name, blob_name, sas_token)
        return url

    def create_container(self):
        """
        Create the blob container.
        """
        self._logger.info("Creating blob container [%s]...", self._blob_container_name)
        blob_service_client, _ = self._get_sync_clients("")

        with blob_service_client:
            try:
                blob_service_client.create_container(self._blob_container_name)
                self._logger.info(
                    "Blob container [%s] successfully created.", self._blob_container_name)
            except ResourceExistsError:
                self._logger.info("Container [%s] already exists.", self._blob_container_name)

    async def create_container_async(self):
        """
        Create the blob container (async).
        """
        self._logger.info("Creating blob container [%s]...", self._blob_container_name)
        blob_service_client_async, _ = self._get_async_clients("")

        try:
            async with blob_service_client_async:
                await blob_service_client_async.create_container(self._blob_container_name)
            self._logger.info(
                "Blob container [%s] successfully created.", self._blob_container_name)
        except ResourceExistsError:
            self._logger.info("Container [%s] already exists.", self._blob_container_name)

    def delete_containers(self):
        """
        Delete all blob containers accessible under the current connection.
        """
        self._logger.info("Deleting all blob containers...")
        blob_service_client, _ = self._get_sync_clients("NonExistent")

        with blob_service_client:
            # TODO: Azurite requires include_metadata=True, otherwise fails with 400 Bad Request
            for container in blob_service_client.list_containers(include_metadata=True):
                self.delete_container(container.name)
            self._logger.info("All blob containers successfully deleted.")

    def delete_container(self, container_name):
        """
        Delete blob container with given name.
        :param container_name: (str)
        """
        self._logger.info("Deleting blob container [%s]...", container_name)
        blob_service_client, _ = self._get_sync_clients("")

        with blob_service_client:
            try:
                blob_service_client.delete_container(container_name)
                self._logger.info("Blob container [%s] successfully deleted.", container_name)
            except ResourceNotFoundError:
                self._logger.warning("Failed to delete container [%s].", container_name)

    def _get_sync_clients(self, blob_name):
        blob_service_client = BlobServiceClient.from_connection_string(
            conn_str=self._blob_connection_string)
        if blob_name:
            blob_client = blob_service_client.get_blob_client(
                container=self._blob_container_name, blob=blob_name)
        else:
            blob_client = None
        return blob_service_client, blob_client

    def _get_async_clients(self, blob_name):
        blob_service_client_async = BlobServiceClientAsync.from_connection_string(
            conn_str=self._blob_connection_string)
        if blob_name:
            blob_client_async = blob_service_client_async.get_blob_client(
                container=self._blob_container_name, blob=blob_name)
        else:
            blob_client_async = None
        return blob_service_client_async, blob_client_async


_blob_manager = None


def get_blob_manager():
    global _blob_manager
    if not _blob_manager:
        _blob_manager = BlobManager(os.environ['BLOB_CONNECTION_STRING'], os.environ['BLOB_CONTAINER_NAME'])
    return _blob_manager