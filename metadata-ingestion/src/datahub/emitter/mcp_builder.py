import hashlib
import json
from typing import Any, Iterable, List, Optional, TypeVar

from pydantic.fields import Field
from pydantic.main import BaseModel

from datahub.emitter.mce_builder import make_container_urn, make_data_platform_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.ingestion.api.workunit import MetadataWorkUnit
from datahub.metadata.com.linkedin.pegasus2avro.common import DataPlatformInstance
from datahub.metadata.com.linkedin.pegasus2avro.container import ContainerProperties
from datahub.metadata.com.linkedin.pegasus2avro.events.metadata import ChangeType
from datahub.metadata.schema_classes import (
    ChangeTypeClass,
    ContainerClass,
    DomainsClass,
    GlobalTagsClass,
    MetadataChangeEventClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
    StatusClass,
    SubTypesClass,
    TagAssociationClass,
    _Aspect,
)
from datahub.utilities.urns.urn import guess_entity_type


class DatahubKey(BaseModel):
    def guid(self) -> str:
        nonnull_dict = self.dict(by_alias=True, exclude_none=True)
        json_key = json.dumps(
            nonnull_dict,
            separators=(",", ":"),
            sort_keys=True,
            cls=DatahubKeyJSONEncoder,
        )
        md5_hash = hashlib.md5(json_key.encode("utf-8"))
        return str(md5_hash.hexdigest())


class PlatformKey(DatahubKey):
    platform: str
    instance: Optional[str] = None


class DatabaseKey(PlatformKey):
    database: str


class SchemaKey(DatabaseKey):
    db_schema: str = Field(alias="schema")


class ProjectIdKey(PlatformKey):
    project_id: str


class BigQueryDatasetKey(ProjectIdKey):
    dataset_id: str


class FolderKey(PlatformKey):
    folder_abs_path: str


class S3BucketKey(PlatformKey):
    bucket_name: str


class DatahubKeyJSONEncoder(json.JSONEncoder):

    # overload method default
    def default(self, obj: Any) -> Any:
        if hasattr(obj, "guid"):
            return obj.guid()
        # Call the default method for other types
        return json.JSONEncoder.default(self, obj)


KeyType = TypeVar("KeyType", bound=PlatformKey)


def add_domain_to_entity_wu(
    entity_type: str, entity_urn: str, domain_urn: str
) -> Iterable[MetadataWorkUnit]:
    mcp = MetadataChangeProposalWrapper(
        entityType=entity_type,
        changeType=ChangeTypeClass.UPSERT,
        entityUrn=f"{entity_urn}",
        aspect=DomainsClass(domains=[domain_urn]),
    )
    wu = MetadataWorkUnit(id=f"{domain_urn}-to-{entity_urn}", mcp=mcp)
    yield wu


def add_owner_to_entity_wu(
    entity_type: str, entity_urn: str, owner_urn: str
) -> Iterable[MetadataWorkUnit]:
    mcp = MetadataChangeProposalWrapper(
        entityType=entity_type,
        changeType=ChangeTypeClass.UPSERT,
        entityUrn=f"{entity_urn}",
        aspect=OwnershipClass(
            owners=[
                OwnerClass(
                    owner=owner_urn,
                    type=OwnershipTypeClass.DATAOWNER,
                )
            ]
        ),
    )
    wu = MetadataWorkUnit(id=f"{owner_urn}-to-{entity_urn}", mcp=mcp)
    yield wu


def add_tags_to_entity_wu(
    entity_type: str, entity_urn: str, tags: List[str]
) -> Iterable[MetadataWorkUnit]:
    mcp = MetadataChangeProposalWrapper(
        entityType=entity_type,
        changeType=ChangeTypeClass.UPSERT,
        entityUrn=f"{entity_urn}",
        aspect=GlobalTagsClass(
            tags=[TagAssociationClass(f"urn:li:tag:{tag}") for tag in tags]
        ),
    )
    wu = MetadataWorkUnit(id=f"tags-to-{entity_urn}", mcp=mcp)
    yield wu


def wrap_aspect_as_workunit(
    entityName: str,
    entityUrn: str,
    aspectName: str,
    aspect: _Aspect,
) -> MetadataWorkUnit:
    wu = MetadataWorkUnit(
        id=f"{aspectName}-for-{entityUrn}",
        mcp=MetadataChangeProposalWrapper(
            entityType=entityName,
            entityUrn=entityUrn,
            aspectName=aspectName,
            aspect=aspect,
            changeType=ChangeType.UPSERT,
        ),
    )
    return wu


def gen_containers(
    container_key: KeyType,
    name: str,
    sub_types: List[str],
    parent_container_key: Optional[PlatformKey] = None,
    domain_urn: Optional[str] = None,
    description: Optional[str] = None,
    owner_urn: Optional[str] = None,
    external_url: Optional[str] = None,
    tags: Optional[List[str]] = None,
    qualified_name: Optional[str] = None,
) -> Iterable[MetadataWorkUnit]:
    container_urn = make_container_urn(
        guid=container_key.guid(),
    )
    mcp = MetadataChangeProposalWrapper(
        entityType="container",
        changeType=ChangeTypeClass.UPSERT,
        entityUrn=f"{container_urn}",
        # entityKeyAspect=ContainerKeyClass(guid=schema_container_key.guid()),
        aspect=ContainerProperties(
            name=name,
            description=description,
            customProperties=container_key.dict(exclude_none=True, by_alias=True),
            externalUrl=external_url,
            qualifiedName=qualified_name,
        ),
    )
    wu = MetadataWorkUnit(id=f"container-info-{name}-{container_urn}", mcp=mcp)
    yield wu

    # add status
    yield wrap_aspect_as_workunit(
        entityName="container",
        entityUrn=f"{container_urn}",
        aspect=StatusClass(removed=False),
        aspectName=StatusClass.get_aspect_name(),
    )

    mcp = MetadataChangeProposalWrapper(
        entityType="container",
        changeType=ChangeTypeClass.UPSERT,
        entityUrn=f"{container_urn}",
        # entityKeyAspect=ContainerKeyClass(guid=schema_container_key.guid()),
        aspect=DataPlatformInstance(
            platform=f"{make_data_platform_urn(container_key.platform)}",
        ),
    )
    wu = MetadataWorkUnit(
        id=f"container-platforminstance-{name}-{container_urn}", mcp=mcp
    )
    yield wu

    # Set subtype
    subtype_mcp = MetadataChangeProposalWrapper(
        entityType="container",
        changeType=ChangeTypeClass.UPSERT,
        entityUrn=f"{container_urn}",
        # entityKeyAspect=ContainerKeyClass(guid=schema_container_key.guid()),
        aspect=SubTypesClass(typeNames=sub_types),
    )
    wu = MetadataWorkUnit(
        id=f"container-subtypes-{name}-{container_urn}", mcp=subtype_mcp
    )
    yield wu

    if domain_urn:
        yield from add_domain_to_entity_wu(
            entity_type="container",
            entity_urn=container_urn,
            domain_urn=domain_urn,
        )

    if owner_urn:
        yield from add_owner_to_entity_wu(
            entity_type="container",
            entity_urn=container_urn,
            owner_urn=owner_urn,
        )

    if tags:
        yield from add_tags_to_entity_wu(
            entity_type="container",
            entity_urn=container_urn,
            tags=sorted(tags),
        )

    if parent_container_key:
        parent_container_urn = make_container_urn(
            guid=parent_container_key.guid(),
        )

        # Set database container
        parent_container_mcp = MetadataChangeProposalWrapper(
            entityType="container",
            changeType=ChangeTypeClass.UPSERT,
            entityUrn=f"{container_urn}",
            # entityKeyAspect=ContainerKeyClass(guid=schema_container_key.guid()),
            aspect=ContainerClass(container=parent_container_urn),
            # aspect=ContainerKeyClass(guid=database_container_key.guid())
        )
        wu = MetadataWorkUnit(
            id=f"container-parent-container-{name}-{container_urn}-{parent_container_urn}",
            mcp=parent_container_mcp,
        )

        yield wu


def add_dataset_to_container(
    # FIXME: Union requires two or more type arguments
    container_key: KeyType,
    dataset_urn: str,
) -> Iterable[MetadataWorkUnit]:
    container_urn = make_container_urn(
        guid=container_key.guid(),
    )

    mcp = MetadataChangeProposalWrapper(
        entityType="dataset",
        changeType=ChangeTypeClass.UPSERT,
        entityUrn=f"{dataset_urn}",
        aspect=ContainerClass(container=f"{container_urn}"),
        # aspect=ContainerKeyClass(guid=schema_container_key.guid())
    )
    wu = MetadataWorkUnit(id=f"container-{container_urn}-to-{dataset_urn}", mcp=mcp)
    yield wu


def add_entity_to_container(
    container_key: KeyType, entity_type: str, entity_urn: str
) -> Iterable[MetadataWorkUnit]:
    container_urn = make_container_urn(
        guid=container_key.guid(),
    )
    mcp = MetadataChangeProposalWrapper(
        entityType=entity_type,
        changeType=ChangeTypeClass.UPSERT,
        entityUrn=entity_urn,
        aspect=ContainerClass(container=f"{container_urn}"),
    )
    wu = MetadataWorkUnit(id=f"container-{container_urn}-to-{entity_urn}", mcp=mcp)
    yield wu


def mcps_from_mce(
    mce: MetadataChangeEventClass,
) -> Iterable[MetadataChangeProposalWrapper]:
    for aspect in mce.proposedSnapshot.aspects:
        yield MetadataChangeProposalWrapper(
            entityType=guess_entity_type(mce.proposedSnapshot.urn),
            changeType=ChangeTypeClass.UPSERT,
            entityUrn=mce.proposedSnapshot.urn,
            auditHeader=mce.auditHeader,
            aspect=aspect,
            systemMetadata=mce.systemMetadata,
        )
