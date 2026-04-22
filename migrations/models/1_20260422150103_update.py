from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS `moderation_logs` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `action` VARCHAR(7) NOT NULL COMMENT 'APPROVE: approve\nREJECT: reject\nFLAG: flag\nFORWARD: forward',
    `reason` LONGTEXT,
    `acted_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `moderator_id` CHAR(36) NOT NULL,
    `post_id` CHAR(36) NOT NULL,
    CONSTRAINT `fk_moderati_users_58830edb` FOREIGN KEY (`moderator_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_moderati_posts_7186483b` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`) ON DELETE CASCADE
) CHARACTER SET utf8mb4 COMMENT='Audit trail of every moderation action. Visible to admin only.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS `moderation_logs`;"""
