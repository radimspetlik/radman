import abc
import logging
import os
import platform
import subprocess

from azure.core.credentials import AzureNamedKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.data.tables import TableServiceClient
from azure.data.tables._base_client import TablesBaseClient
from azure.data.tables.aio import TableServiceClient as TableServiceClientAsync
from azure.data.tables.aio._base_client_async import AsyncTablesBaseClient

from app.azure_constants import AzureConstants
from app.emulator_patch import _batch_send, _batch_send_async


class ITableManager(abc.ABC):

    @abc.abstractmethod
    def upload_batch_to_table(self, table_name, batch):
        pass

    @abc.abstractmethod
    def get_entity(self, table_name, partition_key, row_key):
        pass

    @abc.abstractmethod
    def query_entities(self, table_name, query):
        pass

    @abc.abstractmethod
    def delete_entities(self, table_name, entities):
        pass

    @abc.abstractmethod
    def create_table(self, table_name):
        pass

    @abc.abstractmethod
    def delete_table(self, table_name):
        pass


class TableManager(ITableManager):
    _AZURE_ENDPOINT = "https://{0}.table.core.windows.net"
    _EMULATOR_ENDPOINT = "http://127.0.0.1:10002/{0}"

    def __init__(self, account_name, account_key):
        self._logger = logging.getLogger("PyCloud")

        self._account_name = account_name
        self._account_key = account_key
        self._credential = AzureNamedKeyCredential(account_name, account_key)

        self._emulator = self._account_name == AzureConstants.AZURE_STORAGE_EMULATOR_ACCOUNT_NAME
        if self._emulator:
            TablesBaseClient._batch_send = _batch_send
            AsyncTablesBaseClient._batch_send = _batch_send_async

    def upload_batch_to_table(self, table_name, batch):
        """
        Upload record batch to Azure Table Storage table.
        :param table_name: (str)
        :param batch: (list)
        """
        self._logger.info("Uploading batch of length [%s] to table [%s].", len(batch), table_name)

        table_service, table_client = self._get_sync_clients(table_name)
        operations = []

        for entity in batch:
            operations.append(("upsert", entity))

        with table_service:
            with table_client:
                table_client.submit_transaction(operations)

        self._logger.info("Batch successfully uploaded.")

    async def upload_batch_to_table_async(self, table_name, batch):
        """
        Upload record batch to Azure Table Storage table (async).
        :param table_name: (str)
        :param batch: (list)
        """
        self._logger.info("Uploading batch of length [%s] to table [%s].", len(batch), table_name)

        table_service_async, table_client_async = self._get_async_clients(table_name)
        operations = []

        for entity in batch:
            operations.append(("upsert", entity))

        async with table_service_async:
            async with table_client_async:
                await table_client_async.submit_transaction(operations)

        self._logger.info("Batch successfully uploaded.")

    def get_entity(self, table_name, partition_key, row_key):
        """
        Get entity from Azure Table Storage table.
        :param table_name: (str)
        :param partition_key: (str)
        :param row_key: (str)
        :return: (dict)
        """
        self._logger.info("Loading entity with PartitionKey [%s] and RowKey [%s] from Table [%s].",
                          partition_key, row_key, table_name)

        table_service, table_client = self._get_sync_clients(table_name)

        with table_service:
            with table_client:
                try:
                    return table_client.get_entity(partition_key, row_key)
                except HttpResponseError:
                    self._logger.warning("Entity not found.")
                    return None

    async def get_entity_async(self, table_name, partition_key, row_key):
        """
        Get entity from Azure Table Storage table (async).
        :param table_name: (str)
        :param partition_key: (str)
        :param row_key: (str)
        :return: (dict)
        """
        self._logger.info("Loading entity with PartitionKey [%s] and RowKey [%s] from Table [%s].",
                          partition_key, row_key, table_name)

        table_service_async, table_client_async = self._get_async_clients(table_name)

        async with table_service_async:
            async with table_client_async:
                try:
                    return await table_client_async.get_entity(partition_key, row_key)
                except HttpResponseError:
                    self._logger.warning("Entity not found.")
                    return None

    def query_entities(self, table_name, query=None):
        """
        Query entities from Azure Table Storage table.
        :param table_name: (str)
        :param query: (str)
        :return: (Iterable of dict)
        """
        self._logger.info("Querying entities with Query [%s] from Table [%s].", query, table_name)

        table_service, table_client = self._get_sync_clients(table_name)

        with table_service:
            with table_client:
                for entity in table_client.query_entities(query_filter=query):
                    yield entity

    async def query_entities_async(self, table_name, query=None):
        """
        Query entities from Azure Table Storage table (async).
        :param table_name: (str)
        :param query: (str)
        :return: (Iterable of dict)
        """
        self._logger.info("Querying entities with Query [%s] from Table [%s].", query, table_name)

        table_service_async, table_client_async = self._get_async_clients(table_name)

        async with table_service_async:
            async with table_client_async:
                async for entity in table_client_async.query_entities(query_filter=query):
                    yield entity

    def delete_entities(self, table_name, entities):
        """
        Delete entities from Azure Table Storage table.
        :param table_name: (str)
        :param entities: (list)
        """
        self._logger.info("Deleting [%s] entities from table [%s].", len(entities), table_name)

        table_service, table_client = self._get_sync_clients(table_name)

        with table_service:
            with table_client:
                for entity in entities:
                    table_client.delete_entity(entity)

        self._logger.info("Entities successfully deleted.")

    async def delete_entities_async(self, table_name, entities):
        """
        Delete entities from Azure Table Storage table (async).
        :param table_name: (str)
        :param entities: (list)
        """
        self._logger.info("Deleting [%s] entities from table [%s].", len(entities), table_name)

        table_service_async, table_client_async = self._get_async_clients(table_name)

        async with table_service_async:
            async with table_client_async:
                for entity in entities:
                    await table_client_async.delete_entity(entity)

        self._logger.info("Entities successfully deleted.")

    def create_table(self, table_name):
        """
        Create Azure Table Storage table.
        :param table_name: (str)
        """
        self._logger.info("Creating table [%s].", table_name)

        table_service, _table_client = self._get_sync_clients(table_name)
        with table_service:
            table_service.create_table_if_not_exists(table_name)

        self._logger.info("Table [%s] successfully created.", table_name)

    def delete_table(self, table_name):
        """
        Delete Azure Table Storage table.
        :param table_name: (str)
        """
        self._logger.info("Deleting table [%s].", table_name)

        table_service, _table_client = self._get_sync_clients(table_name)
        with table_service:
            table_service.delete_table(table_name)

        self._logger.info("Table [%s] successfully deleted.", table_name)

    def _get_sync_clients(self, table_name):
        endpoint = self._EMULATOR_ENDPOINT if self._emulator else self._AZURE_ENDPOINT

        table_service = TableServiceClient(
            endpoint=endpoint.format(self._account_name), credential=self._credential)
        table_client = table_service.get_table_client(table_name)
        return table_service, table_client

    def _get_async_clients(self, table_name):
        endpoint = self._EMULATOR_ENDPOINT if self._emulator else self._AZURE_ENDPOINT

        table_service_async = TableServiceClientAsync(
            endpoint=endpoint.format(self._account_name), credential=self._credential)
        table_client_async = table_service_async.get_table_client(table_name)
        return table_service_async, table_client_async

    def _is_azure_storage_emulator_running(self):
        if self._is_windows():
            return self._process_exists("AzureStorageEmulator.exe")
        return False

    @staticmethod
    def _is_windows():
        return platform.system() == "Windows"

    @staticmethod
    def _process_exists(process_name):
        call = "TASKLIST", "/FI", "imagename eq %s" % process_name
        output = subprocess.check_output(call).decode()
        last_line = output.strip().split("\r\n")[-1]
        return last_line.lower().startswith(process_name.lower())


_table_manager = None


def get_table_manager():
    global _table_manager
    if not _table_manager:
        _table_manager = TableManager(os.environ['STORAGE_ACCOUNT_NAME'], os.environ['STORAGE_ACCOUNT_KEY'])
    return _table_manager
