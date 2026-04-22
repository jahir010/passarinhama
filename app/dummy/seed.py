# from datetime import datetime, timedelta, timezone

# from applications.communication.models import ChatSession, Message, Notification
# from applications.feedback.models import ReviewAndRating
# from applications.jobs.models import CancellationRequest, Post, Proposal, SavedPost, Task
# from applications.profiles.models import Education, Language, PaymentInfo, StoreProfile, VAProfile, WorkExperience
# from applications.site.models import CookiesPolicy, Policy, SiteReview, Terms
# from applications.user.models import Group, Permission, User, UserRole
# from app.dummy.users import create_test_users


# def _utc_now() -> datetime:
#     return datetime.now(timezone.utc)


# async def _ensure_user(
#     *,
#     email: str,
#     password: str,
#     first_name: str,
#     last_name: str,
#     role: UserRole,
#     is_superuser: bool = False,
#     is_active: bool = True,
# ) -> User:
#     defaults = {
#         "first_name": first_name,
#         "last_name": last_name,
#         "role": role,
#         "is_superuser": is_superuser,
#         "is_active": is_active,
#         "password": User.set_password(password),
#     }
#     user, created = await User.get_or_create(email=email, defaults=defaults)
#     updated = created is False

#     if updated:
#         dirty = False
#         for field, value in defaults.items():
#             if field == "password":
#                 if not user.verify_password(password):
#                     user.password = value
#                     dirty = True
#                 continue
#             if getattr(user, field) != value:
#                 setattr(user, field, value)
#                 dirty = True
#         if dirty:
#             await user.save()
#     return user


# async def _ensure_group_with_permissions(name: str, codenames: list[str]) -> Group:
#     group, _ = await Group.get_or_create(name=name)
#     permissions = await Permission.filter(codename__in=codenames)
#     await group.permissions.clear()
#     if permissions:
#         await group.permissions.add(*permissions)
#     return group


# async def _seed_users() -> dict[str, User]:
#     await create_test_users()
#     admin = await _ensure_user(
#         email="admin@gmail.com",
#         password="admin",
#         first_name="Admin",
#         last_name="User",
#         is_superuser=True,
#     )
#     merchant = await _ensure_user(
#         email="merchant@example.com",
#         password="merchant123",
#         first_name="Mona",
#         last_name="Merchant",
#         role=UserRole.MERCHANT,
#     )
#     va = await _ensure_user(
#         email="va@example.com",
#         password="va123456",
#         first_name="Victor",
#         last_name="Assistant",
#         role=UserRole.VIRTUAL_ASSISTANT,
#     )
#     support = await _ensure_user(
#         email="support@example.com",
#         password="support123",
#         first_name="Sara",
#         last_name="Support",
#         role=UserRole.MERCHANT,
#     )

#     admin_group = await _ensure_group_with_permissions(
#         "Admins",
#         [
#             "view_user",
#             "add_user",
#             "update_user",
#             "delete_user",
#             "view_group",
#             "add_group",
#             "update_group",
#             "delete_group",
#             "view_permission",
#         ],
#     )
#     support_group = await _ensure_group_with_permissions(
#         "Support",
#         ["view_user", "update_user", "view_permission"],
#     )

#     await admin.groups.clear()
#     await admin.groups.add(admin_group)
#     await support.groups.clear()
#     await support.groups.add(support_group)

#     return {
#         "admin": admin,
#         "merchant": merchant,
#         "va": va,
#         "support": support,
#     }


# async def _seed_profiles(users: dict[str, User]) -> dict[str, object]:
#     now = _utc_now()
#     va_profile, _ = await VAProfile.get_or_create(
#         va=users["va"],
#         defaults={
#             "professional_role": "Amazon VA",
#             "job_title": "Senior Virtual Assistant",
#             "skills": ["product research", "listing optimization", "customer support"],
#             "bio": "Experienced virtual assistant for ecommerce operations.",
#             "hourly_rate": 18.5,
#             "country": "Bangladesh",
#             "city": "Dhaka",
#             "phone": "+8801700000000",
#             "current_balance": 250.0,
#             "date_of_birth": now - timedelta(days=365 * 27),
#         },
#     )
#     store_profile, _ = await StoreProfile.get_or_create(
#         merchant=users["merchant"],
#         defaults={
#             "company_name": "Merchant Hub",
#             "category": "Ecommerce",
#             "contact_email": users["merchant"].email,
#             "phone": "+8801800000000",
#             "description": "Sample merchant storefront profile.",
#             "website": "https://example.com/store",
#             "owner_full_name": "Mona Merchant",
#             "nid_or_passport": "NID123456789",
#             "email_verified": True,
#             "phone_verified": True,
#             "KYC_documents": True,
#         },
#     )
#     payment_info, _ = await PaymentInfo.get_or_create(
#         profile=store_profile,
#         defaults={
#             "account_name": "Merchant Hub Ltd",
#             "account_number": "012345678901",
#             "bank_name": "Sample Bank",
#         },
#     )
#     work_experience, _ = await WorkExperience.get_or_create(
#         profile=va_profile,
#         title="Ecommerce Virtual Assistant",
#         defaults={
#             "company_name": "Marketplace Ops",
#             "location": "Remote",
#             "country": "Bangladesh",
#             "currently_working_this_role": True,
#             "start_date": now - timedelta(days=365 * 2),
#             "description": "Handled listings, support, and fulfillment workflows.",
#         },
#     )
#     education, _ = await Education.get_or_create(
#         profile=va_profile,
#         school="Dhaka Commerce College",
#         defaults={
#             "degree": "BBA",
#             "field_of_study": "Management",
#             "date_from": now - timedelta(days=365 * 7),
#             "date_to": now - timedelta(days=365 * 3),
#             "description": "Business and operations studies.",
#         },
#     )
#     language, _ = await Language.get_or_create(
#         profile=va_profile,
#         name="English",
#         defaults={"proficiency_level": "Advanced"},
#     )

#     return {
#         "va_profile": va_profile,
#         "store_profile": store_profile,
#         "payment_info": payment_info,
#         "work_experience": work_experience,
#         "education": education,
#         "language": language,
#     }


# async def _seed_jobs(users: dict[str, User]) -> dict[str, object]:
#     now = _utc_now()
#     post, _ = await Post.get_or_create(
#         title="Amazon Listing Optimization",
#         merchant=users["merchant"],
#         va=users["va"],
#         defaults={
#             "description": "Improve listing quality and keyword coverage.",
#             "task_type": "listing",
#             "experience_level": "intermediate",
#             "status": "open",
#             "budget": 250.0,
#             "category": "Amazon",
#             "required_skills": ["seo", "listing", "ecommerce"],
#             "estimate_duration": 14,
#             "attachedments": [{"name": "brief.pdf"}],
#             "end_date": now + timedelta(days=14),
#         },
#     )
#     task, _ = await Task.get_or_create(
#         post=post,
#         title="Keyword Research",
#         defaults={
#             "description": "Prepare primary and secondary keywords.",
#             "per_task_budget": 75.0,
#             "status": "not started",
#             "due_date": now + timedelta(days=5),
#         },
#     )
#     proposal, _ = await Proposal.get_or_create(
#         va=users["va"],
#         post=post,
#         defaults={
#             "expected_price": 225.0,
#             "expected_timeline": now + timedelta(days=10),
#             "cover_leter": "I can improve the listing with marketplace-focused SEO.",
#             "attached_file": "proposal.pdf",
#         },
#     )
#     saved_post, _ = await SavedPost.get_or_create(user=users["support"], post=post)
#     cancellation_request, _ = await CancellationRequest.get_or_create(
#         va=users["va"],
#         post=post,
#         defaults={
#             "reason": "Client changed scope",
#             "additional_details": "Requested cancellation after kickoff.",
#         },
#     )

#     return {
#         "post": post,
#         "task": task,
#         "proposal": proposal,
#         "saved_post": saved_post,
#         "cancellation_request": cancellation_request,
#     }


# async def _seed_communication(users: dict[str, User]) -> dict[str, object]:
#     now = _utc_now()
#     session, _ = await ChatSession.get_or_create(
#         user1=users["merchant"],
#         user2=users["va"],
#         defaults={
#             "is_active": True,
#             "last_message_at": now,
#         },
#     )
#     message, _ = await Message.get_or_create(
#         from_user=users["merchant"],
#         to_user=users["va"],
#         text="Can you share a timeline for the listing work?",
#         defaults={
#             "from_name": "Mona Merchant",
#             "to_name": "Victor Assistant",
#             "is_delivered": True,
#             "reactions": [{"emoji": "thumbs_up", "by": str(users["va"].id)}],
#         },
#     )
#     notification, _ = await Notification.get_or_create(
#         user=users["va"],
#         title="New project message",
#         defaults={
#             "body": "You have a new message from Mona Merchant.",
#             "is_read": False,
#         },
#     )
#     return {
#         "chat_session": session,
#         "message": message,
#         "notification": notification,
#     }


# async def _seed_feedback(users: dict[str, User]) -> dict[str, object]:
#     review, _ = await ReviewAndRating.get_or_create(
#         reviewee=users["va"],
#         reviewer=users["merchant"],
#         defaults={
#             "review": "Great communication and clear execution.",
#             "ratings": 4.8,
#         },
#     )
#     return {"review_and_rating": review}


# async def _seed_site(users: dict[str, User]) -> dict[str, object]:
#     terms, _ = await Terms.get_or_create(
#         title="Platform Terms",
#         defaults={"details": "Sample terms and conditions for development use."},
#     )
#     policy, _ = await Policy.get_or_create(
#         title="Privacy Policy",
#         defaults={"details": "Sample privacy policy for development use."},
#     )
#     cookies, _ = await CookiesPolicy.get_or_create(
#         title="Cookies Policy",
#         defaults={"details": "Sample cookies policy for development use."},
#     )
#     site_review, _ = await SiteReview.get_or_create(
#         user=users["support"],
#         defaults={
#             "rating": 5,
#             "comment": "Helpful workflows and clear API structure.",
#         },
#     )
#     return {
#         "terms": terms,
#         "policy": policy,
#         "cookies_policy": cookies,
#         "site_review": site_review,
#     }


# async def seed_all_dummy_data() -> None:
#     users = await _seed_users()
#     profiles = await _seed_profiles(users)
#     jobs = await _seed_jobs(users)
#     communication = await _seed_communication(users)
#     feedback = await _seed_feedback(users)
#     site = await _seed_site(users)

#     print(
#         "[dummy] seeding completed "
#         f"(users={len(users)}, profiles={len(profiles)}, jobs={len(jobs)}, "
#         f"communication={len(communication)}, feedback={len(feedback)}, site={len(site)})"
#     )
