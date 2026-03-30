from __future__ import annotations

from typing import Any, Mapping
from datetime import datetime, timedelta
import os

import urllib3
import boto3
from botocore.config import Config

from sentry.utils.codecs import Codec, ZstdCodec
from sentry.nodestore.base import NodeStorage
from sentry.nodestore.django import DjangoNodeStorage


class S3PassthroughDjangoNodeStorage(DjangoNodeStorage, NodeStorage):
    compression_strategies: Mapping[str, Codec[bytes, bytes]] = {
        "zstd": ZstdCodec(),
    }

    def __init__(
            self,
            delete_through=False,
            write_through=False,
            read_through=False,
            compression=True,
            bucket_name=None,
            region_name=None,
            bucket_path=None,
            endpoint_url=None,
            retry_attempts=3,
            aws_access_key_id=None,
            aws_secret_access_key=None,
            signature_version=None,
            use_ssl=True,
            ca_bundle_path=os.getenv('DEFAULT_CA_BUNDLE', None),
            skip_tls_verify=False,
            object_sharding=False,
            object_sharding_fallback=False,
    ):
        self.delete_through = delete_through
        self.write_through = write_through
        self.read_through = read_through

        if compression:
            self.compression = "zstd"
        else:
            self.compression = None

        if ca_bundle_path:
            verify = ca_bundle_path
        else:
            verify = not skip_tls_verify

        if verify == False:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        self.bucket_name = bucket_name
        self.bucket_path = bucket_path
        self.client = boto3.client(
            config=Config(
                retries={
                    'mode': 'standard',
                    'max_attempts': retry_attempts,
                },
                signature_version=signature_version,
            ),
            region_name=region_name,
            service_name='s3',
            endpoint_url=endpoint_url,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            use_ssl=use_ssl,
            verify=verify,
        )

        self.object_sharding = object_sharding
        self.object_sharding_fallback = object_sharding_fallback

    def delete(self, id):
        if self.delete_through:
            super().delete(id)
        self.__delete_from_bucket(id, self.object_sharding)
        self._delete_cache_item(id)

    def _get_bytes(self, id: str) -> bytes | None:
        if self.read_through:
            return self.__read_from_bucket(id, self.object_sharding) or super()._get_bytes(id)
        return self.__read_from_bucket(id, self.object_sharding)

    def _get_bytes_multi(self, id_list: list[str]) -> dict[str, bytes | None]:
        return {id: self._get_bytes(id) for id in id_list}

    def delete_multi(self, id_list: list[str]) -> None:
        if self.delete_through:
            super().delete_multi(id_list)
        # TODO: Maybe we should use the bulk delete API of the S3 client instead
        for id in id_list:
            self.__delete_from_bucket(id, self.object_sharding)
        self._delete_cache_items(id_list)

    def _set_bytes(self, id: str, data: Any, ttl: timedelta | None = None) -> None:
        if self.write_through:
            super()._set_bytes(id, data, ttl)
        self.__write_to_bucket(id, data)

    def cleanup(self, cutoff_timestamp: datetime) -> None:
        if self.delete_through:
            super().cleanup(cutoff_timestamp)

    def __get_key_for_id(self, id: str, sharding: bool = False) -> str:
        if self.bucket_path is None:
            return self.__format_id(id, sharding)
        return self.bucket_path + '/' + self.__format_id(id, sharding)

    def __format_id(self, id: str, sharding: bool = False) -> str:
        if sharding:
            return '/'.join(part for part in (id[:2], id[2:6], id[6:]) if part)
        return id

    def __read_from_bucket(self, id: str, sharding: bool = False) -> bytes | None:
        try:
            obj = self.client.get_object(
                Key=self.__get_key_for_id(id, sharding),
                Bucket=self.bucket_name,
            )

            data = obj.get('Body').read()

            codec = self.compression_strategies.get(obj.get('ContentEncoding'))

            return codec.decode(data) if codec else data
        except self.client.exceptions.NoSuchKey:
            if sharding and self.object_sharding_fallback:
                return self.__read_from_bucket(id, False)
            return None

    def __write_to_bucket(self, id: str, data: Any) -> None:
        content_encoding = ''

        if self.compression is not None:
            codec = self.compression_strategies[self.compression]
            compressed_data = codec.encode(data)

            # Check if compression is worth it, otherwise store the data uncompressed
            if len(compressed_data) <= len(data):
                data = compressed_data
                content_encoding = self.compression

        self.client.put_object(
            Key=self.__get_key_for_id(id, self.object_sharding),
            Body=data,
            Bucket=self.bucket_name,
            ContentEncoding=content_encoding,
        )

    def __delete_from_bucket(self, id: str, sharding: bool = False) -> None:
        try:
            self.client.delete_object(
                Key=self.__get_key_for_id(id, sharding),
                Bucket=self.bucket_name,
            )
        except self.client.exceptions.NoSuchKey:
            pass

        if sharding and self.object_sharding_fallback:
            self.__delete_from_bucket(id, False)
