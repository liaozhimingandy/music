"""dal 层：数据访问（SQLite3 + 任务产物文件），不含业务逻辑。

四个模块的职责：
* ``database``   连接 / 事务 / 建表 / UTC 时间戳
* ``song_dal``   songs 表 CRUD
* ``stage_dal``  song_stages 表进度读写
* ``artifact_dal`` 阶段 JSON 与音频文件的存取（断点续跑依据）

依赖方向：本层只能引用 models 与 config，不可反向 import services / routers。
这样替换数据库实现时，Service 层无需改动。
"""

from .artifact_dal import ArtifactStore
from .database import connection, database_path, init_schema, utc_now
from .song_dal import SongDAL
from .stage_dal import StageDAL

__all__ = [
    "ArtifactStore",
    "SongDAL",
    "StageDAL",
    "connection",
    "database_path",
    "init_schema",
    "utc_now",
]
