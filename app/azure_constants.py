class AzureConstants:
    APPLICATION_INSIGHTS_CONNECTION_STRING = (
        "InstrumentationKey=12345678-1234-5678-abcd-12345678abcd")
    AZURE_STORAGE_EMULATOR_ACCOUNT_NAME = "devstoreaccount1"
    AZURE_STORAGE_EMULATOR_ACCOUNT_KEY = (
        "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==")
    AZURE_STORAGE_EMULATOR_CONNECTION_STRING = (
        "DefaultEndpointsProtocol=http;"
        "AccountName={0};"
        "AccountKey={1};"
        "BlobEndpoint=http://127.0.0.1:10000/{0};"
        "QueueEndpoint=http://127.0.0.1:10001/{0};"
        "TableEndpoint=http://127.0.0.1:10001/{0};"
    ).format(AZURE_STORAGE_EMULATOR_ACCOUNT_NAME, AZURE_STORAGE_EMULATOR_ACCOUNT_KEY)
