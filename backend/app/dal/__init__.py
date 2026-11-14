"""dal 层：数据访问（SQLite3 + 任务产物文件），不含业务逻辑。

四个模块的职责：
* ``database``   异步长连接 / 事务锁 / 建表补列 / UTC 时间戳
* ``song_dal``   songs 表 CRUD、去重与活跃任务计数、持久化队列扫描与认领
* ``stage_dal``  song_stages 表进度读写
* ``artifact_dal`` 阶段 JSON 与音频文件的存取（断点续跑依据）

所有 IO 方法均为 async：数据库走 aiosqlite 长连接，文件走
``asyncio.to_thread``。依赖方向：本层只能引用 models 与 config，
不可反向 import services / routers；替换数据库实现时 Service 层无需改动。
"""

from .artifact_dal import ArtifactStore
from .database import close_connection, connection, database_path, init_schema, utc_now
from .song_dal import SongDAL
from .stage_dal import StageDAL

__all__ = [
    "ArtifactStore",
    "SongDAL",
    "StageDAL",
    "connection",
    "close_connection",
    "database_path",
    "init_schema",
    "utc_now",
]
