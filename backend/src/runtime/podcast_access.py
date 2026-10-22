from sqlalchemy import select

from .errors import PodcastError
from .storage.materials import require_material
from .storage.tables import Material, Podcast


def _locked(db, owner, identity, *, allow_deleted=False, read=False):
    material_id = db.scalar(
        select(Podcast.material_id).where(
            Podcast.podcast_id == identity, Podcast.learner_id == owner
        )
    )
    if material_id is None:
        raise PodcastError("RESOURCE_NOT_FOUND")
    if read:
        # 固定 revision 的唯讀驗證可能耗時；不持有教材鎖阻擋生成結果保存。
        material = db.scalar(
            select(Material).where(
                Material.material_id == material_id,
                Material.learner_id == owner,
                Material.discard_requested_at.is_(None),
            )
        )
        if material is None:
            raise PodcastError("RESOURCE_NOT_FOUND")
    else:
        material = require_material(db, owner, material_id)
    row = db.get(Podcast, identity, populate_existing=True)
    if row is None or (row.status == "deleted" and not allow_deleted):
        raise PodcastError("RESOURCE_NOT_FOUND")
    return row, material
