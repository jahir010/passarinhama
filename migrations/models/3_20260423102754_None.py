from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS `article_categories` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `name` VARCHAR(100) NOT NULL UNIQUE,
    `color_code` VARCHAR(7) NOT NULL DEFAULT '#FFD600',
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) CHARACTER SET utf8mb4 COMMENT='Lookup: Reform, Association, Training, Technology, Legal, Other.';
CREATE TABLE IF NOT EXISTS `forums` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `name` VARCHAR(200) NOT NULL UNIQUE,
    `slug` VARCHAR(200) NOT NULL UNIQUE,
    `description` LONGTEXT,
    `forum_type` VARCHAR(50) NOT NULL DEFAULT 'general',
    `is_active` BOOL NOT NULL DEFAULT 1,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) CHARACTER SET utf8mb4 COMMENT='Forum container. Access controlled via ForumRolePermission.';
CREATE TABLE IF NOT EXISTS `forum_role_permissions` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `role` VARCHAR(23) NOT NULL COMMENT 'ADMIN: admin\nMODERATOR: moderator\nMEMBRE: membre\nAUDITEUR: auditeur\nPARTENAIRES_TECHNICOPRO: partenaires_technicopro\nCOMITE_COPROPRIETAIRE: comite_coproprietaire',
    `can_read` BOOL NOT NULL DEFAULT 0,
    `can_post` BOOL NOT NULL DEFAULT 0,
    `forum_id` CHAR(36) NOT NULL,
    UNIQUE KEY `uid_forum_role__forum_i_355bc0` (`forum_id`, `role`),
    CONSTRAINT `fk_forum_ro_forums_b0fa2b09` FOREIGN KEY (`forum_id`) REFERENCES `forums` (`id`) ON DELETE CASCADE
) CHARACTER SET utf8mb4 COMMENT='Per-forum per-role access control matrix.';
CREATE TABLE IF NOT EXISTS `group` (
    `id` INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `name` VARCHAR(100) NOT NULL UNIQUE
) CHARACTER SET utf8mb4;
CREATE TABLE IF NOT EXISTS `membership_categories` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `name` VARCHAR(100) NOT NULL UNIQUE,
    `code` VARCHAR(20) NOT NULL UNIQUE,
    `description` LONGTEXT,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) CHARACTER SET utf8mb4 COMMENT='Lookup table: Category A (Senior), Category B (Member), Category C (Associate).';
CREATE TABLE IF NOT EXISTS `permission` (
    `id` INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `name` VARCHAR(100) NOT NULL UNIQUE,
    `codename` VARCHAR(100) NOT NULL UNIQUE
) CHARACTER SET utf8mb4;
CREATE TABLE IF NOT EXISTS `users` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `email` VARCHAR(255) NOT NULL UNIQUE,
    `password` VARCHAR(255) NOT NULL,
    `first_name` VARCHAR(100) NOT NULL,
    `last_name` VARCHAR(100) NOT NULL,
    `phone` VARCHAR(30),
    `avatar_url` VARCHAR(500),
    `street_address` VARCHAR(255),
    `city` VARCHAR(100),
    `postal_code` VARCHAR(20),
    `country` VARCHAR(100) NOT NULL DEFAULT 'France',
    `role` VARCHAR(23) NOT NULL COMMENT 'ADMIN: admin\nMODERATOR: moderator\nMEMBRE: membre\nAUDITEUR: auditeur\nPARTENAIRES_TECHNICOPRO: partenaires_technicopro\nCOMITE_COPROPRIETAIRE: comite_coproprietaire' DEFAULT 'auditeur',
    `status` VARCHAR(9) NOT NULL COMMENT 'PENDING: pending\nACTIVE: active\nSUSPENDED: suspended' DEFAULT 'pending',
    `is_active` BOOL NOT NULL DEFAULT 1,
    `is_superuser` BOOL NOT NULL DEFAULT 0,
    `is_active_2fa` BOOL NOT NULL DEFAULT 0,
    `is_payment_validated` BOOL NOT NULL DEFAULT 0,
    `validated_at` DATETIME(6),
    `is_deleted` BOOL NOT NULL DEFAULT 0,
    `member_since` DATETIME(6),
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `membership_category_id` CHAR(36),
    `validated_by_id` CHAR(36),
    CONSTRAINT `fk_users_membersh_2bd3a255` FOREIGN KEY (`membership_category_id`) REFERENCES `membership_categories` (`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_users_users_1daf84ad` FOREIGN KEY (`validated_by_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
    KEY `idx_users_status_86eb78` (`status`, `is_payment_validated`)
) CHARACTER SET utf8mb4 COMMENT='Core member account. Covers all roles: admin, moderator, membre,';
CREATE TABLE IF NOT EXISTS `articles` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `title` VARCHAR(500) NOT NULL,
    `excerpt` LONGTEXT,
    `body` LONGTEXT,
    `pdf_url` VARCHAR(500),
    `youtube_url` VARCHAR(500),
    `structured_fields` JSON,
    `status` VARCHAR(9) NOT NULL COMMENT 'DRAFT: draft\nPUBLISHED: published' DEFAULT 'draft',
    `published_at` DATETIME(6),
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `author_id` CHAR(36) NOT NULL,
    `category_id` CHAR(36) NOT NULL,
    CONSTRAINT `fk_articles_users_3b493172` FOREIGN KEY (`author_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_articles_article__f2c2d886` FOREIGN KEY (`category_id`) REFERENCES `article_categories` (`id`) ON DELETE RESTRICT
) CHARACTER SET utf8mb4 COMMENT='Editorial content with ACF-style structured fields.';
CREATE TABLE IF NOT EXISTS `topics` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `title` VARCHAR(500) NOT NULL,
    `is_pinned` BOOL NOT NULL DEFAULT 0,
    `is_locked` BOOL NOT NULL DEFAULT 0,
    `view_count` INT NOT NULL DEFAULT 0,
    `reply_count` INT NOT NULL DEFAULT 0,
    `last_activity_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `author_id` CHAR(36) NOT NULL,
    `forum_id` CHAR(36) NOT NULL,
    CONSTRAINT `fk_topics_users_1f21c74b` FOREIGN KEY (`author_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_topics_forums_8db803f5` FOREIGN KEY (`forum_id`) REFERENCES `forums` (`id`) ON DELETE CASCADE
) CHARACTER SET utf8mb4 COMMENT='Discussion thread inside a Forum.';
CREATE TABLE IF NOT EXISTS `posts` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `content` LONGTEXT NOT NULL,
    `moderation_status` VARCHAR(8) NOT NULL COMMENT 'PENDING: pending\nAPPROVED: approved\nREJECTED: rejected\nFLAGGED: flagged' DEFAULT 'pending',
    `rejection_reason` LONGTEXT,
    `moderated_at` DATETIME(6),
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `assigned_moderator_id` CHAR(36),
    `author_id` CHAR(36) NOT NULL,
    `moderated_by_id` CHAR(36),
    `topic_id` CHAR(36) NOT NULL,
    CONSTRAINT `fk_posts_users_53162e58` FOREIGN KEY (`assigned_moderator_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_posts_users_63d1e9cc` FOREIGN KEY (`author_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_posts_users_89ad3ef8` FOREIGN KEY (`moderated_by_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_posts_topics_191d1155` FOREIGN KEY (`topic_id`) REFERENCES `topics` (`id`) ON DELETE CASCADE
) CHARACTER SET utf8mb4 COMMENT='Individual reply within a Topic.';
CREATE TABLE IF NOT EXISTS `moderation_logs` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `action` VARCHAR(7) NOT NULL COMMENT 'APPROVE: approve\nREJECT: reject\nFLAG: flag\nFORWARD: forward',
    `reason` LONGTEXT,
    `acted_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `moderator_id` CHAR(36) NOT NULL,
    `post_id` CHAR(36) NOT NULL,
    CONSTRAINT `fk_moderati_users_58830edb` FOREIGN KEY (`moderator_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_moderati_posts_7186483b` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`) ON DELETE CASCADE
) CHARACTER SET utf8mb4 COMMENT='Audit trail of every moderation action. Visible to admin only.';
CREATE TABLE IF NOT EXISTS `notification_logs` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `notification_type` VARCHAR(16) NOT NULL COMMENT 'NEW_ARTICLE: new_article\nNEW_POST: new_post\nNEW_EVENT: new_event\nNEW_TRAINING: new_training\nPOST_REPLY: post_reply\nPOST_REJECTED: post_rejected\nACCOUNT_APPROVED: account_approved',
    `target_type` VARCHAR(50) NOT NULL,
    `target_id` CHAR(36),
    `is_read` BOOL NOT NULL DEFAULT 0,
    `sent_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `recipient_id` CHAR(36) NOT NULL,
    CONSTRAINT `fk_notifica_users_c1bc52e6` FOREIGN KEY (`recipient_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) CHARACTER SET utf8mb4 COMMENT='Audit log of every email dispatched by the platform.';
CREATE TABLE IF NOT EXISTS `notification_preferences` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `notification_type` VARCHAR(16) NOT NULL COMMENT 'NEW_ARTICLE: new_article\nNEW_POST: new_post\nNEW_EVENT: new_event\nNEW_TRAINING: new_training\nPOST_REPLY: post_reply\nPOST_REJECTED: post_rejected\nACCOUNT_APPROVED: account_approved',
    `email_enabled` BOOL NOT NULL DEFAULT 1,
    `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    `forum_id` CHAR(36),
    `user_id` CHAR(36) NOT NULL,
    UNIQUE KEY `uid_notificatio_user_id_a9861d` (`user_id`, `notification_type`, `forum_id`),
    CONSTRAINT `fk_notifica_forums_0530b32f` FOREIGN KEY (`forum_id`) REFERENCES `forums` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_notifica_users_a1b632bd` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) CHARACTER SET utf8mb4 COMMENT='Per-user per-type email opt-in/out.';
CREATE TABLE IF NOT EXISTS `activity_logs` (
    `id` CHAR(36) NOT NULL PRIMARY KEY,
    `action_type` VARCHAR(19) NOT NULL COMMENT 'USER_REGISTERED: user_registered\nUSER_VALIDATED: user_validated\nARTICLE_PUBLISHED: article_published\nPOST_CREATED: post_created\nPOST_APPROVED: post_approved\nPOST_REJECTED: post_rejected\nTOPIC_CREATED: topic_created\nEVENT_CREATED: event_created\nTRAINING_REGISTERED: training_registered\nDOCUMENT_UPLOADED: document_uploaded\nMODERATION_FLAG: moderation_flag\nPOST_FORWARDED: post_forwarded',
    `target_type` VARCHAR(50),
    `target_id` CHAR(36),
    `description` LONGTEXT,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `user_id` CHAR(36) NOT NULL,
    CONSTRAINT `fk_activity_users_ca9d02cb` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) CHARACTER SET utf8mb4 COMMENT='Platform-wide activity feed (polymorphic: target_type + target_id).';
CREATE TABLE IF NOT EXISTS `aerich` (
    `id` INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `version` VARCHAR(255) NOT NULL,
    `app` VARCHAR(100) NOT NULL,
    `content` JSON NOT NULL
) CHARACTER SET utf8mb4;
CREATE TABLE IF NOT EXISTS `group_permissions` (
    `group_id` INT NOT NULL,
    `permission_id` INT NOT NULL,
    FOREIGN KEY (`group_id`) REFERENCES `group` (`id`) ON DELETE CASCADE,
    FOREIGN KEY (`permission_id`) REFERENCES `permission` (`id`) ON DELETE CASCADE,
    UNIQUE KEY `uidx_group_permi_group_i_c7a36c` (`group_id`, `permission_id`)
) CHARACTER SET utf8mb4;
CREATE TABLE IF NOT EXISTS `user_permissions` (
    `users_id` CHAR(36) NOT NULL,
    `permission_id` INT NOT NULL,
    FOREIGN KEY (`users_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    FOREIGN KEY (`permission_id`) REFERENCES `permission` (`id`) ON DELETE CASCADE,
    UNIQUE KEY `uidx_user_permis_users_i_035bf3` (`users_id`, `permission_id`)
) CHARACTER SET utf8mb4;
CREATE TABLE IF NOT EXISTS `user_groups` (
    `users_id` CHAR(36) NOT NULL,
    `group_id` INT NOT NULL,
    FOREIGN KEY (`users_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    FOREIGN KEY (`group_id`) REFERENCES `group` (`id`) ON DELETE CASCADE,
    UNIQUE KEY `uidx_user_groups_users_i_7ef143` (`users_id`, `group_id`)
) CHARACTER SET utf8mb4;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """
