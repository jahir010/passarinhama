from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE `posts` ADD `assigned_moderator_id` CHAR(36);
        ALTER TABLE `activity_logs` MODIFY COLUMN `action_type` VARCHAR(19) NOT NULL COMMENT 'USER_REGISTERED: user_registered\nUSER_VALIDATED: user_validated\nARTICLE_PUBLISHED: article_published\nPOST_CREATED: post_created\nPOST_APPROVED: post_approved\nPOST_REJECTED: post_rejected\nTOPIC_CREATED: topic_created\nEVENT_CREATED: event_created\nTRAINING_REGISTERED: training_registered\nDOCUMENT_UPLOADED: document_uploaded\nMODERATION_FLAG: moderation_flag\nPOST_FORWARDED: post_forwarded';
        ALTER TABLE `posts` ADD CONSTRAINT `fk_posts_users_53162e58` FOREIGN KEY (`assigned_moderator_id`) REFERENCES `users` (`id`) ON DELETE SET NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE `posts` DROP FOREIGN KEY `fk_posts_users_53162e58`;
        ALTER TABLE `posts` DROP COLUMN `assigned_moderator_id`;
        ALTER TABLE `activity_logs` MODIFY COLUMN `action_type` VARCHAR(19) NOT NULL COMMENT 'USER_REGISTERED: user_registered\nUSER_VALIDATED: user_validated\nARTICLE_PUBLISHED: article_published\nPOST_CREATED: post_created\nPOST_APPROVED: post_approved\nPOST_REJECTED: post_rejected\nTOPIC_CREATED: topic_created\nEVENT_CREATED: event_created\nTRAINING_REGISTERED: training_registered\nDOCUMENT_UPLOADED: document_uploaded\nMODERATION_FLAG: moderation_flag';"""
