# -*- coding: utf-8 -*-
"""
Tests for the `edx-enterprise` api module.
"""

import base64
import json
import uuid
from operator import itemgetter
from smtplib import SMTPException

import ddt
import mock
from pytest import mark, raises
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.test import APIClient
from six.moves.urllib.parse import (  # pylint: disable=import-error,ungrouped-imports
    parse_qs,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

from django.conf import settings
from django.contrib.auth.models import Permission
from django.test import override_settings
from django.utils import timezone

from enterprise.api.v1.views import LicensedEnterpriseCourseEnrollmentViewSet
from enterprise.constants import (
    ALL_ACCESS_CONTEXT,
    ENTERPRISE_ADMIN_ROLE,
    ENTERPRISE_DASHBOARD_ADMIN_ROLE,
    ENTERPRISE_LEARNER_ROLE,
    ENTERPRISE_OPERATOR_ROLE,
    ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE,
)
from enterprise.models import (
    EnterpriseCatalogQuery,
    EnterpriseCourseEnrollment,
    EnterpriseCustomerUser,
    EnterpriseEnrollmentSource,
    EnterpriseFeatureRole,
    EnterpriseFeatureUserRoleAssignment,
    PendingEnrollment,
    PendingEnterpriseCustomerUser,
)
from enterprise.utils import NotConnectedToOpenEdX
from enterprise_learner_portal.utils import CourseRunProgressStatuses
from test_utils import (
    FAKE_UUIDS,
    TEST_COURSE,
    TEST_COURSE_KEY,
    TEST_PASSWORD,
    TEST_SLUG,
    TEST_USERNAME,
    APITest,
    factories,
    fake_catalog_api,
    fake_enterprise_api,
    update_course_run_with_enterprise_context,
    update_course_with_enterprise_context,
    update_program_with_enterprise_context,
)
from test_utils.factories import FAKER
from test_utils.fake_enterprise_api import get_default_branding_object

ENTERPRISE_CATALOGS_LIST_ENDPOINT = reverse('enterprise-catalogs-list')
ENTERPRISE_CATALOGS_DETAIL_ENDPOINT = reverse(
    'enterprise-catalogs-detail',
    kwargs={'pk': FAKE_UUIDS[1]}
)
ENTERPRISE_CATALOGS_CONTAINS_CONTENT_ENDPOINT = reverse(
    'enterprise-catalogs-contains-content-items',
    kwargs={'pk': FAKE_UUIDS[1]}
)
ENTERPRISE_CATALOGS_COURSE_ENDPOINT = reverse(
    'enterprise-catalogs-course-detail',
    kwargs={'pk': FAKE_UUIDS[1], 'course_key': TEST_COURSE_KEY}
)
ENTERPRISE_CATALOGS_COURSE_RUN_ENDPOINT = reverse(
    'enterprise-catalogs-course-run-detail',
    kwargs={'pk': FAKE_UUIDS[1], 'course_id': TEST_COURSE}
)
ENTERPRISE_CATALOGS_PROGRAM_ENDPOINT = reverse(
    'enterprise-catalogs-program-detail', kwargs={'pk': FAKE_UUIDS[1], 'program_uuid': FAKE_UUIDS[3]}
)
ENTERPRISE_COURSE_ENROLLMENT_LIST_ENDPOINT = reverse('enterprise-course-enrollment-list')
ENTERPRISE_CUSTOMER_BRANDING_LIST_ENDPOINT = reverse('enterprise-customer-branding-list')
ENTERPRISE_CUSTOMER_BRANDING_DETAIL_ENDPOINT = reverse('enterprise-customer-branding-detail', (TEST_SLUG,))
ENTERPRISE_CUSTOMER_LIST_ENDPOINT = reverse('enterprise-customer-list')
ENTERPRISE_CUSTOMER_BASIC_LIST_ENDPOINT = reverse('enterprise-customer-basic-list')
ENTERPRISE_CUSTOMER_CONTAINS_CONTENT_ENDPOINT = reverse(
    'enterprise-customer-contains-content-items',
    kwargs={'pk': FAKE_UUIDS[0]}
)
ENTERPRISE_CUSTOMER_COURSE_ENROLLMENTS_ENDPOINT = reverse('enterprise-customer-course-enrollments', (FAKE_UUIDS[0],))
ENTERPRISE_CUSTOMER_ENTERPRISE_LEARNERS_ENDPOINT = reverse('enterprise-customer-enterprise-learners', (FAKE_UUIDS[0],))
ENTERPRISE_CUSTOMER_REPORTING_ENDPOINT = reverse('enterprise-customer-reporting-list')
ENTERPRISE_LEARNER_LIST_ENDPOINT = reverse('enterprise-learner-list')
ENTERPRISE_CUSTOMER_WITH_ACCESS_TO_ENDPOINT = reverse('enterprise-customer-with-access-to')
PENDING_ENTERPRISE_LEARNER_LIST_ENDPOINT = reverse('pending-enterprise-learner-list')
LICENSED_ENTERPISE_COURSE_ENROLLMENTS_REVOKE_ENDPOINT = reverse(
    'licensed-enterprise-course-enrollment-license-revoke'
)
EXPIRED_LICENSED_ENTERPRISE_COURSE_ENROLLMENTS_ENDPOINT = reverse(
    'licensed-enterprise-course-enrollment-bulk-licensed-enrollments-expiration'
)


def side_effect(url, query_parameters):
    """
    returns a url with updated query parameters.
    """
    if any(key in ['utm_medium', 'catalog'] for key in query_parameters):
        return url

    scheme, netloc, path, query_string, fragment = urlsplit(url)
    url_params = parse_qs(query_string)

    # Update url query parameters
    url_params.update(query_parameters)

    return urlunsplit(
        (scheme, netloc, path, urlencode(url_params, doseq=True), fragment),
    )


@ddt.ddt
@mark.django_db
class TestEnterpriseAPIViews(APITest):
    """
    Tests for enterprise api views.
    """
    # Get current datetime, so that all tests can use same datetime.
    now = timezone.now()
    maxDiff = None

    def setUp(self):
        super().setUp()
        self.set_jwt_cookie(ENTERPRISE_OPERATOR_ROLE, ALL_ACCESS_CONTEXT)

    # pylint: disable=arguments-differ
    def create_user(self, username=TEST_USERNAME, password=TEST_PASSWORD, is_staff=True, **kwargs):
        """
        Create a test user and set its password.
        """
        self.user = factories.UserFactory(username=username, is_active=True, is_staff=is_staff, **kwargs)
        self.user.set_password(password)  # pylint: disable=no-member
        self.user.save()  # pylint: disable=no-member

    def create_items(self, factory, items):
        """
        Create model instances using given factory
        """
        for item in items:
            factory.create(**item)

    @override_settings(ECOMMERCE_SERVICE_WORKER_USERNAME=TEST_USERNAME)
    @mock.patch("enterprise.api.v1.serializers.track_enrollment")
    @ddt.data(
        (
            # A valid request.
            True,
            [
                factories.EnterpriseCustomerUserFactory,
                [{
                    'id': 1, 'user_id': 0,
                    'enterprise_customer__uuid': FAKE_UUIDS[0],
                    'enterprise_customer__name': 'Test Enterprise Customer',
                    'enterprise_customer__active': True, 'enterprise_customer__enable_data_sharing_consent': True,
                    'enterprise_customer__enforce_data_sharing_consent': 'at_enrollment',
                    'enterprise_customer__site__domain': 'example.com',
                    'enterprise_customer__site__name': 'example.com',
                }]
            ],
            {
                'username': TEST_USERNAME,
                'course_id': 'course-v1:edX+DemoX+DemoCourse',
            },
            False,
            201
        ),
        (
            # A valid request to an existing enrollment.
            True,
            [
                factories.EnterpriseCustomerUserFactory,
                [{
                    'id': 1, 'user_id': 0,
                    'enterprise_customer__uuid': FAKE_UUIDS[0],
                    'enterprise_customer__name': 'Test Enterprise Customer',
                    'enterprise_customer__active': True, 'enterprise_customer__enable_data_sharing_consent': True,
                    'enterprise_customer__enforce_data_sharing_consent': 'at_enrollment',
                    'enterprise_customer__site__domain': 'example.com',
                    'enterprise_customer__site__name': 'example.com',
                }]
            ],
            {
                'username': TEST_USERNAME,
                'course_id': 'course-v1:edX+DemoX+DemoCourse',
            },
            True,
            201
        ),
        (
            # A bad request due to an invalid user.
            True,
            [
                factories.EnterpriseCustomerUserFactory,
                [{
                    'id': 1, 'user_id': 0,
                    'enterprise_customer__uuid': FAKE_UUIDS[0],
                    'enterprise_customer__name': 'Test Enterprise Customer',
                    'enterprise_customer__active': True, 'enterprise_customer__enable_data_sharing_consent': True,
                    'enterprise_customer__enforce_data_sharing_consent': 'at_enrollment',
                    'enterprise_customer__site__domain': 'example.com',
                    'enterprise_customer__site__name': 'example.com',
                }]
            ],
            {
                'username': 'does_not_exist',
                'course_id': 'course-v1:edX+DemoX+DemoCourse',
            },
            False,
            400
        ),
        (
            # A rejected request due to missing model permissions.
            False,
            [
                factories.EnterpriseCustomerUserFactory,
                [{
                    'id': 1, 'user_id': 0,
                    'enterprise_customer__uuid': FAKE_UUIDS[0],
                    'enterprise_customer__name': 'Test Enterprise Customer',
                    'enterprise_customer__active': True, 'enterprise_customer__enable_data_sharing_consent': True,
                    'enterprise_customer__enforce_data_sharing_consent': 'at_enrollment',
                    'enterprise_customer__site__domain': 'example.com',
                    'enterprise_customer__site__name': 'example.com',
                }]
            ],
            {
                'username': TEST_USERNAME,
                'consent_granted': True,
                'course_id': 'course-v1:edX+DemoX+DemoCourse',
            },
            False,
            403
        ),
        (
            # A bad request due to a non-existing EnterpriseCustomerUser.
            True,
            [
                factories.EnterpriseCustomerFactory,
                [{
                    'uuid': FAKE_UUIDS[0], 'name': 'Test Enterprise Customer',
                    'active': True, 'enable_data_sharing_consent': True,
                    'enforce_data_sharing_consent': 'at_enrollment',
                    'site__domain': 'example.com', 'site__name': 'example.com',
                }]
            ],
            {
                'username': TEST_USERNAME,
                'course_id': 'course-v1:edX+DemoX+DemoCourse',
            },
            False,
            400
        )
    )
    @ddt.unpack
    def test_post_enterprise_course_enrollment(
            self,
            has_permissions,
            factory,
            request_data,
            enrollment_exists,
            status_code,
            mock_track_enrollment,
    ):
        """
        Make sure service users can post new EnterpriseCourseEnrollments.
        """
        factory_type, factory_data = factory
        if factory_type == factories.EnterpriseCustomerUserFactory:
            factory_data[0]['user_id'] = self.user.pk  # pylint: disable=no-member

        self.create_items(*factory)
        if has_permissions:
            permission = Permission.objects.get(name='Can add enterprise course enrollment')
            self.user.user_permissions.add(permission)

        if enrollment_exists:
            enterprise_customer_user = EnterpriseCustomerUser.objects.get(user_id=self.user.pk)
            EnterpriseCourseEnrollment.objects.create(
                enterprise_customer_user=enterprise_customer_user,
                course_id=request_data['course_id'],
            )

        response = self.client.post(
            settings.TEST_SERVER + ENTERPRISE_COURSE_ENROLLMENT_LIST_ENDPOINT,
            data=request_data
        )
        assert response.status_code == status_code
        response = self.load_json(response.content)

        if status_code == 201:
            enterprise_customer_user = EnterpriseCustomerUser.objects.get(user_id=self.user.pk)
            enrollment = EnterpriseCourseEnrollment.objects.get(
                enterprise_customer_user=enterprise_customer_user,
                course_id=request_data['course_id'],
            )

            self.assertDictEqual(request_data, response)
            if enrollment_exists:
                mock_track_enrollment.assert_not_called()
                assert enrollment.source is None
            else:
                mock_track_enrollment.assert_called_once_with(
                    'rest-api-enrollment',
                    self.user.id,
                    request_data['course_id'],
                )
                assert enrollment.source.slug == EnterpriseEnrollmentSource.OFFER_REDEMPTION
        else:
            mock_track_enrollment.assert_not_called()

    def test_get_enterprise_customer_user_contains_consent_records(self):
        user = factories.UserFactory()
        enterprise_customer = factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0])
        factories.EnterpriseCustomerUserFactory(
            user_id=user.id,
            enterprise_customer=enterprise_customer
        )
        factories.DataSharingConsentFactory(
            username=user.username,
            enterprise_customer=enterprise_customer,
            course_id=TEST_COURSE,
            granted=True
        )

        expected_json = [{
            'username': user.username,
            'enterprise_customer_uuid': FAKE_UUIDS[0],
            'exists': True,
            'course_id': TEST_COURSE,
            'consent_provided': True,
            'consent_required': False
        }]

        response = self.client.get(
            '{host}{path}?username={username}'.format(
                host=settings.TEST_SERVER,
                path=ENTERPRISE_LEARNER_LIST_ENDPOINT,
                username=user.username
            )
        )
        response = self.load_json(response.content)
        assert expected_json == response['results'][0]['data_sharing_consent_records']

    def test_get_enterprise_customer_user_with_groups(self):
        user = factories.UserFactory()
        group1 = factories.GroupFactory(name='enterprise_enrollment_api_access')
        group1.user_set.add(user)
        group2 = factories.GroupFactory(name='some_other_group')
        group2.user_set.add(user)
        enterprise_customer = factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0])
        factories.EnterpriseCustomerUserFactory(
            user_id=user.id,
            enterprise_customer=enterprise_customer
        )

        expected_groups = ['enterprise_enrollment_api_access']

        response = self.client.get(
            '{host}{path}?username={username}'.format(
                host=settings.TEST_SERVER,
                path=ENTERPRISE_LEARNER_LIST_ENDPOINT,
                username=user.username
            )
        )
        response = self.load_json(response.content)
        assert expected_groups == response['results'][0]['groups']

    @override_settings(ECOMMERCE_SERVICE_WORKER_USERNAME=TEST_USERNAME)
    @ddt.data(
        (True, 201),
        (False, 403)
    )
    @ddt.unpack
    def test_post_enterprise_customer_user(self, has_permissions, status_code):
        """
        Make sure service users can post new EnterpriseCustomerUsers.
        """
        factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0])
        data = {
            'enterprise_customer': FAKE_UUIDS[0],
            'username': TEST_USERNAME,
        }
        if has_permissions:
            permission = Permission.objects.get(name='Can add Enterprise Customer Learner')
            self.user.user_permissions.add(permission)

        response = self.client.post(settings.TEST_SERVER + ENTERPRISE_LEARNER_LIST_ENDPOINT, data=data)
        assert response.status_code == status_code
        response = self.load_json(response.content)

        if status_code == 201:
            data.update({'active': True})
            self.assertDictEqual(data, response)

    def test_post_enterprise_customer_user_logged_out(self):
        """
        Make sure users can't post EnterpriseCustomerUsers when logged out.
        """
        self.client.logout()
        self.create_items(
            factories.EnterpriseCustomerFactory,
            [{
                'uuid': FAKE_UUIDS[0], 'name': 'Test Enterprise Customer',
                'active': True, 'enable_data_sharing_consent': True,
                'enforce_data_sharing_consent': 'at_enrollment',
                'site__domain': 'example.com', 'site__name': 'example.com',
            }]
        )
        data = {
            'enterprise_customer': FAKE_UUIDS[0],
            'username': self.user.username
        }
        response = self.client.post(settings.TEST_SERVER + ENTERPRISE_LEARNER_LIST_ENDPOINT, data=data)
        assert response.status_code == 401

    @ddt.data(
        {'is_staff': True, 'user_exists': True, 'ecu_exists': True, 'pending_ecu_exists': False, 'status_code': 204},
        {'is_staff': True, 'user_exists': True, 'ecu_exists': False, 'pending_ecu_exists': True, 'status_code': 201},
        {'is_staff': True, 'user_exists': False, 'ecu_exists': False, 'pending_ecu_exists': True, 'status_code': 204},
        {'is_staff': True, 'user_exists': False, 'ecu_exists': False, 'pending_ecu_exists': False, 'status_code': 201},
        {'is_staff': True, 'user_exists': True, 'ecu_exists': False, 'pending_ecu_exists': False, 'status_code': 201},
        {'is_staff': False, 'user_exists': False, 'ecu_exists': False, 'pending_ecu_exists': False, 'status_code': 403},
    )
    @ddt.unpack
    def test_post_pending_enterprise_customer_user(
            self,
            is_staff,
            user_exists,
            ecu_exists,
            pending_ecu_exists,
            status_code):
        """
        Make sure service users can post new PendingEnterpriseCustomerUsers.
        """
        client_username = 'client_username'
        self.client.logout()
        self.create_user(username=client_username, password=TEST_PASSWORD, is_staff=is_staff)
        self.client.login(username=client_username, password=TEST_PASSWORD)

        enterprise_customer = factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0])
        new_user_email = 'newuser@example.com'
        data = {
            'enterprise_customer': FAKE_UUIDS[0],
            'user_email': new_user_email,
        }
        if user_exists:
            user = factories.UserFactory(email=new_user_email)
            if ecu_exists:
                factories.EnterpriseCustomerUserFactory(user_id=user.id, enterprise_customer=enterprise_customer)

        if pending_ecu_exists:
            factories.PendingEnterpriseCustomerUserFactory(
                user_email=new_user_email, enterprise_customer=enterprise_customer
            )

        response = self.client.post(settings.TEST_SERVER + PENDING_ENTERPRISE_LEARNER_LIST_ENDPOINT, data=data)
        assert response.status_code == status_code
        if status_code == 204:
            assert not response.content
        elif status_code == 201:
            response = self.load_json(response.content)
            self.assertDictEqual(data, response)
            if not user_exists:
                assert PendingEnterpriseCustomerUser.objects.get(
                    user_email=new_user_email, enterprise_customer=enterprise_customer
                )
            else:
                assert EnterpriseCustomerUser.objects.get(
                    user_id=user.id, enterprise_customer=enterprise_customer, active=user.is_active
                )

    def test_post_pending_enterprise_customer_user_logged_out(self):
        """
        Make sure users can't post PendingEnterpriseCustomerUsers when logged out.
        """
        self.client.logout()
        data = {
            'enterprise_customer': FAKE_UUIDS[0],
            'username': self.user.username
        }
        response = self.client.post(settings.TEST_SERVER + PENDING_ENTERPRISE_LEARNER_LIST_ENDPOINT, data=data)
        assert response.status_code == 401

    @ddt.data(
        (
            factories.EnterpriseCustomerFactory,
            ENTERPRISE_CUSTOMER_LIST_ENDPOINT,
            itemgetter('uuid'),
            [{
                'uuid': FAKE_UUIDS[0], 'name': 'Test Enterprise Customer', 'slug': TEST_SLUG,
                'active': True, 'enable_data_sharing_consent': True,
                'enforce_data_sharing_consent': 'at_enrollment', 'enable_audit_data_reporting': True,
                'site__domain': 'example.com', 'site__name': 'example.com',
                'contact_email': 'fake@example.com',
            }],
            [{
                'uuid': FAKE_UUIDS[0], 'name': 'Test Enterprise Customer', 'slug': TEST_SLUG,
                'active': True, 'enable_data_sharing_consent': True,
                'enforce_data_sharing_consent': 'at_enrollment',
                'branding_configuration': get_default_branding_object(FAKE_UUIDS[0], TEST_SLUG),
                'enable_audit_enrollment': False, 'enable_audit_data_reporting': True, 'identity_provider': None,
                'replace_sensitive_sso_username': False, 'enable_portal_code_management_screen': False,
                'enable_portal_reporting_config_screen': False,
                'enable_portal_saml_configuration_screen': False,
                'enable_portal_lms_configurations_screen': False,
                'site': {
                    'domain': 'example.com', 'name': 'example.com'
                },
                'sync_learner_profile_data': False,
                'enable_learner_portal': False,
                'enable_integrated_customer_learner_portal_search': True,
                'enable_portal_subscription_management_screen': False,
                'enable_analytics_screen': False,
                'contact_email': 'fake@example.com',
                'hide_course_original_price': False,
            }],
        ),
        (
            factories.EnterpriseCustomerUserFactory,
            ENTERPRISE_LEARNER_LIST_ENDPOINT,
            itemgetter('user_id'),
            [{
                'id': 1, 'user_id': 0,
                'enterprise_customer__uuid': FAKE_UUIDS[0],
                'enterprise_customer__name': 'Test Enterprise Customer',
                'enterprise_customer__slug': TEST_SLUG,
                'enterprise_customer__active': True, 'enterprise_customer__enable_data_sharing_consent': True,
                'enterprise_customer__enforce_data_sharing_consent': 'at_enrollment',
                'enterprise_customer__site__domain': 'example.com',
                'enterprise_customer__site__name': 'example.com',
                'enterprise_customer__contact_email': 'fake@example.com',

            }],
            [{
                'id': 1, 'user_id': 0, 'user': None, 'active': True, 'data_sharing_consent_records': [], 'groups': [],
                'enterprise_customer': {
                    'uuid': FAKE_UUIDS[0], 'name': 'Test Enterprise Customer', 'slug': TEST_SLUG,
                    'active': True, 'enable_data_sharing_consent': True,
                    'enforce_data_sharing_consent': 'at_enrollment',
                    'branding_configuration': get_default_branding_object(FAKE_UUIDS[0], TEST_SLUG),
                    'enable_audit_enrollment': False, 'identity_provider': None,
                    'replace_sensitive_sso_username': False, 'enable_portal_code_management_screen': False,
                    'enable_portal_reporting_config_screen': False,
                    'enable_portal_saml_configuration_screen': False,
                    'enable_portal_lms_configurations_screen': False,
                    'enable_audit_data_reporting': False,
                    'site': {
                        'domain': 'example.com', 'name': 'example.com'
                    },
                    'sync_learner_profile_data': False,
                    'enable_learner_portal': False,
                    'enable_integrated_customer_learner_portal_search': True,
                    'enable_portal_subscription_management_screen': False,
                    'enable_analytics_screen': False,
                    'contact_email': 'fake@example.com',
                    'hide_course_original_price': False,
                }
            }],
        ),
        (
            factories.EnterpriseCourseEnrollmentFactory,
            ENTERPRISE_COURSE_ENROLLMENT_LIST_ENDPOINT,
            itemgetter('enterprise_customer_user'),
            [{
                'enterprise_customer_user__id': 1,
                'course_id': 'course-v1:edX+DemoX+DemoCourse',
            }],
            [{
                'enterprise_customer_user': 1,
                'course_id': 'course-v1:edX+DemoX+DemoCourse',
            }],
        ),
        (
            factories.EnterpriseCustomerIdentityProviderFactory,
            ENTERPRISE_CUSTOMER_LIST_ENDPOINT,
            itemgetter('uuid'),
            [{
                'provider_id': FAKE_UUIDS[0],
                'enterprise_customer__uuid': FAKE_UUIDS[1],
                'enterprise_customer__name': 'Test Enterprise Customer',
                'enterprise_customer__slug': TEST_SLUG,
                'enterprise_customer__active': True, 'enterprise_customer__enable_data_sharing_consent': True,
                'enterprise_customer__enforce_data_sharing_consent': 'at_enrollment',
                'enterprise_customer__site__domain': 'example.com',
                'enterprise_customer__site__name': 'example.com',
                'enterprise_customer__contact_email': 'fake@example.com',

            }],
            [{
                'uuid': FAKE_UUIDS[1], 'name': 'Test Enterprise Customer', 'slug': TEST_SLUG,
                'active': True, 'enable_data_sharing_consent': True,
                'enforce_data_sharing_consent': 'at_enrollment',
                'branding_configuration': get_default_branding_object(FAKE_UUIDS[1], TEST_SLUG),
                'enable_audit_enrollment': False, 'identity_provider': FAKE_UUIDS[0],
                'replace_sensitive_sso_username': False, 'enable_portal_code_management_screen': False,
                'enable_portal_reporting_config_screen': False,
                'enable_portal_saml_configuration_screen': False,
                'enable_portal_lms_configurations_screen': False,
                'enable_audit_data_reporting': False,
                'site': {
                    'domain': 'example.com', 'name': 'example.com'
                },
                'sync_learner_profile_data': False,
                'enable_learner_portal': False,
                'enable_integrated_customer_learner_portal_search': True,
                'enable_portal_subscription_management_screen': False,
                'enable_analytics_screen': False,
                'contact_email': 'fake@example.com',
                'hide_course_original_price': False,
            }],
        ),
        (
            factories.EnterpriseCustomerBrandingConfigurationFactory,
            ENTERPRISE_CUSTOMER_BRANDING_LIST_ENDPOINT,
            itemgetter('enterprise_customer'),
            [{
                'enterprise_customer__uuid': FAKE_UUIDS[0],
                'enterprise_customer__slug': TEST_SLUG,
                'logo': 'enterprise/branding/1/1_logo.png',
                'primary_color': '#000000',
                'secondary_color': '#ffffff',
                'tertiary_color': '#888888',
            }],
            [{
                'enterprise_customer': FAKE_UUIDS[0],
                'enterprise_slug': TEST_SLUG,
                'logo': settings.LMS_ROOT_URL + settings.MEDIA_URL + 'enterprise/branding/1/1_logo.png',
                'primary_color': '#000000',
                'secondary_color': '#ffffff',
                'tertiary_color': '#888888',
            }],
        ),
    )
    @ddt.unpack
    @mock.patch('enterprise.utils.get_logo_url')
    def test_api_views(self, factory, url, sorting_key, model_items, expected_json, mock_get_logo_url):
        """
        Make sure API end point returns all of the expected fields.
        """
        mock_get_logo_url.return_value = 'http://fake.url'
        self.create_items(factory, model_items)
        response = self.client.get(settings.TEST_SERVER + url)
        response = self.load_json(response.content)
        assert sorted(expected_json, key=sorting_key) == sorted(response['results'], key=sorting_key)

    def test_enterprise_customer_basic_list(self):
        """
            Test basic list endpoint of enterprise_customers
        """
        url = urljoin(settings.TEST_SERVER, ENTERPRISE_CUSTOMER_BASIC_LIST_ENDPOINT)
        enterprise_customers = [
            {
                'name': FAKER.company(),  # pylint: disable=no-member
                'uuid': str(uuid.uuid4())
            }
            for _ in range(15)
        ]
        self.create_items(factories.EnterpriseCustomerFactory, enterprise_customers)
        # now replace 'uuid' key with 'id'  to match with response.
        for enterprise_customer in enterprise_customers:
            enterprise_customer['id'] = enterprise_customer.pop("uuid")
        sorted_enterprise_customers = sorted(enterprise_customers, key=itemgetter('name'))

        response = self.client.get(url)
        assert sorted_enterprise_customers == self.load_json(response.content)

        # test startswith param
        startswith = 'a'
        startswith_enterprise_customers = [
            customer for customer in sorted_enterprise_customers if customer['name'].lower().startswith(startswith)
        ]
        response = self.client.get(url, {'startswith': startswith})
        assert startswith_enterprise_customers == self.load_json(response.content)

    @ddt.data(
        # Request missing required permissions query param.
        (True, False, [], {}, False, {'detail': 'User is not allowed to access the view.'}),
        # Staff user that does not have the specified group permission.
        (True, False, [], {'permissions': ['enterprise_enrollment_api_access']}, False,
         {'detail': 'User is not allowed to access the view.'}),
        # Staff user that does have the specified group permission.
        (True, False, ['enterprise_enrollment_api_access'], {'permissions': ['enterprise_enrollment_api_access']},
         True, None),
        # Non staff user that is not linked to the enterprise, nor do they have the group permission.
        (False, False, [], {'permissions': ['enterprise_enrollment_api_access']}, False,
         {'detail': 'User is not allowed to access the view.'}),
        # Non staff user that is not linked to the enterprise, but does have the group permission.
        (False, False, ['enterprise_enrollment_api_access'], {'permissions': ['enterprise_enrollment_api_access']},
         False, {'count': 0, 'next': None, 'previous': None, 'results': []}),
        # Non staff user that is linked to the enterprise, but does not have the group permission.
        (False, True, [], {'permissions': ['enterprise_enrollment_api_access']}, False,
         {'detail': 'User is not allowed to access the view.'}),
        # Non staff user that is linked to the enterprise and does have the group permission
        (False, True, ['enterprise_enrollment_api_access'], {'permissions': ['enterprise_enrollment_api_access']},
         True, None),
        # Non staff user that is linked to the enterprise and has group permission and the request has passed
        # multiple groups to check.
        (False, True, ['enterprise_enrollment_api_access'],
         {'permissions': ['enterprise_enrollment_api_access', 'enterprise_data_api_access']}, True, None),
        # Staff user with group permission filtering on non existent enterprise id.
        (True, False, ['enterprise_enrollment_api_access'],
         {'permissions': ['enterprise_enrollment_api_access'], 'enterprise_id': FAKE_UUIDS[1]}, False,
         {'count': 0, 'next': None, 'previous': None, 'results': []}),
        # Staff user with group permission filtering on enterprise id successfully.
        (True, False, ['enterprise_enrollment_api_access'],
         {'permissions': ['enterprise_enrollment_api_access'], 'enterprise_id': FAKE_UUIDS[0]}, True, None),
        # Staff user with group permission filtering on search param with no results.
        (True, False, ['enterprise_enrollment_api_access'],
         {'permissions': ['enterprise_enrollment_api_access'], 'search': 'blah'}, False,
         {'count': 0, 'next': None, 'previous': None, 'results': []}),
        # Staff user with group permission filtering on search param with results.
        (True, False, ['enterprise_enrollment_api_access'],
         {'permissions': ['enterprise_enrollment_api_access'], 'search': 'test'}, True, None),
        # Staff user with group permission filtering on slug with results.
        (True, False, ['enterprise_enrollment_api_access'],
         {'permissions': ['enterprise_enrollment_api_access'], 'slug': TEST_SLUG}, True, None),
        # Staff user with group permissions filtering on slug with no results.
        (True, False, ['enterprise_enrollment_api_access'],
         {'permissions': ['enterprise_enrollment_api_access'], 'slug': 'blah'}, False,
         {'count': 0, 'next': None, 'previous': None, 'results': []}),
    )
    @ddt.unpack
    @mock.patch('enterprise.utils.get_logo_url')
    def test_enterprise_customer_with_access_to(
            self,
            is_staff,
            is_linked_to_enterprise,
            user_groups,
            query_params,
            has_access_to_enterprise,
            expected_error,
            mock_get_logo_url,
    ):
        """
        ``enterprise_customer``'s detail list endpoint ``with_access_to`` should validate permissions
         and serialize the ``EnterpriseCustomer`` objects the user has access to.
        """
        mock_get_logo_url.return_value = 'http://fake.url'
        enterprise_customer_data = {
            'uuid': FAKE_UUIDS[0], 'name': 'Test Enterprise Customer', 'slug': TEST_SLUG,
            'active': True, 'enable_data_sharing_consent': True,
            'enforce_data_sharing_consent': 'at_enrollment', 'enable_portal_code_management_screen': True,
            'enable_portal_reporting_config_screen': False,
            'enable_portal_saml_configuration_screen': False,
            'enable_portal_lms_configurations_screen': False,
            'site__domain': 'example.com', 'site__name': 'example.com',
            'enable_portal_subscription_management_screen': False,
            'enable_analytics_screen': False,
            'contact_email': 'fake@example.com',
        }
        enterprise_customer = factories.EnterpriseCustomerFactory(**enterprise_customer_data)

        # creating a non staff user so verify the insufficient permission conditions.
        user = factories.UserFactory(username='test_user', is_active=True, is_staff=is_staff)
        user.set_password('test_password')  # pylint: disable=no-member
        user.save()  # pylint: disable=no-member

        if is_linked_to_enterprise:
            factories.EnterpriseCustomerUserFactory(
                user_id=user.id,
                enterprise_customer=enterprise_customer,
            )

        for group_name in user_groups:
            group = factories.GroupFactory(name=group_name)
            group.user_set.add(user)

        client = APIClient()
        client.login(username='test_user', password='test_password')

        response = client.get(settings.TEST_SERVER +
                              ENTERPRISE_CUSTOMER_WITH_ACCESS_TO_ENDPOINT +
                              '?' + urlencode(query_params, True))
        response = self.load_json(response.content)
        if has_access_to_enterprise:
            assert response['results'][0] == {
                'uuid': FAKE_UUIDS[0], 'name': 'Test Enterprise Customer', 'slug': TEST_SLUG,
                'active': True, 'enable_data_sharing_consent': True,
                'enforce_data_sharing_consent': 'at_enrollment',
                'branding_configuration': get_default_branding_object(FAKE_UUIDS[0], TEST_SLUG),
                'enable_audit_enrollment': False, 'enable_audit_data_reporting': False, 'identity_provider': None,
                'replace_sensitive_sso_username': False, 'enable_portal_code_management_screen': True,
                'enable_portal_reporting_config_screen': False,
                'enable_portal_saml_configuration_screen': False,
                'enable_portal_lms_configurations_screen': False,
                'site': {
                    'domain': 'example.com', 'name': 'example.com'
                },
                'sync_learner_profile_data': False,
                'enable_learner_portal': False,
                'enable_integrated_customer_learner_portal_search': True,
                'enable_portal_subscription_management_screen': False,
                'enable_analytics_screen': False,
                'contact_email': 'fake@example.com',
                'hide_course_original_price': False,
            }
        else:
            assert response == expected_error

    def test_enterprise_customer_branding_detail(self):
        """
        ``enterprise_customer_branding``'s get endpoint should get the config by looking up the enterprise slug and
         serialize the ``EnterpriseCustomerBrandingConfig``.
        """
        factory = factories.EnterpriseCustomerBrandingConfigurationFactory
        model_items = [
            {
                'enterprise_customer__uuid': FAKE_UUIDS[0],
                'enterprise_customer__slug': TEST_SLUG,
                'logo': 'enterprise/branding/1/1_logo.png',
                'primary_color': '#000000',
                'secondary_color': '#ffffff',
                'tertiary_color': '#888888',
            },
            {
                'enterprise_customer__uuid': FAKE_UUIDS[1],
                'enterprise_customer__slug': 'another-slug',
                'logo': 'enterprise/branding/2/2_logo.png',
                'primary_color': '#000000',
                'secondary_color': '#ffffff',
                'tertiary_color': '#888888',
            },
        ]
        expected_item = {
            'enterprise_customer': FAKE_UUIDS[0],
            'enterprise_slug': TEST_SLUG,
            'logo': settings.LMS_ROOT_URL + settings.MEDIA_URL + 'enterprise/branding/1/1_logo.png',
            'primary_color': '#000000',
            'secondary_color': '#ffffff',
            'tertiary_color': '#888888',
        }
        self.create_items(factory, model_items)
        response = self.client.get(settings.TEST_SERVER + ENTERPRISE_CUSTOMER_BRANDING_DETAIL_ENDPOINT)
        response = self.load_json(response.content)
        assert expected_item == response

    @ddt.data(
        (False, False),
        (False, True),
        (True, False),
    )
    @ddt.unpack
    def test_enterprise_customer_catalogs_list(self, is_staff, is_linked_to_enterprise):
        """
        ``enterprise_catalogs``'s list endpoint should serialize the ``EnterpriseCustomerCatalog`` model.
        """
        catalog_uuid = FAKE_UUIDS[1]
        catalog_title = 'All Content'
        catalog_filter = {}
        enterprise_customer = factories.EnterpriseCustomerFactory(
            uuid=FAKE_UUIDS[0]
        )
        factories.EnterpriseCustomerCatalogFactory(
            uuid=catalog_uuid,
            title=catalog_title,
            enterprise_customer=enterprise_customer,
            content_filter=catalog_filter
        )
        self.user.is_staff = is_staff
        self.user.save()
        if is_linked_to_enterprise:
            factories.EnterpriseCustomerUserFactory(
                user_id=self.user.id,
                enterprise_customer=enterprise_customer,
            )
        if is_staff or is_linked_to_enterprise:
            expected_results = {
                'count': 1,
                'next': None,
                'previous': None,
                'results': [{
                    'uuid': catalog_uuid,
                    'title': catalog_title,
                    'enterprise_customer': enterprise_customer.uuid
                }]
            }
        else:
            expected_results = {
                'count': 0,
                'next': None,
                'previous': None,
                'results': []
            }

        response = self.client.get(ENTERPRISE_CATALOGS_LIST_ENDPOINT)
        response = self.load_json(response.content)

        assert response == expected_results

    @ddt.data(
        (
            False,
            False,
            {'detail': 'Not found.'},
        ),
        (
            False,
            True,
            fake_enterprise_api.build_fake_enterprise_catalog_detail(
                paginated_content=fake_catalog_api.FAKE_SEARCH_ALL_RESULTS,
                include_enterprise_context=True,
                add_utm_info=False,
                count=3,
            ),
        ),
        (
            True,
            False,
            fake_enterprise_api.build_fake_enterprise_catalog_detail(
                paginated_content=fake_catalog_api.FAKE_SEARCH_ALL_RESULTS,
                include_enterprise_context=True,
                add_utm_info=False,
                count=3,
            ),
        ),
        (
            True,
            True,
            fake_enterprise_api.build_fake_enterprise_catalog_detail(
                paginated_content=fake_catalog_api.FAKE_SEARCH_ALL_RESULTS,
                include_enterprise_context=True,
                add_utm_info=False,
                count=3,
            ),
        ),
    )
    @ddt.unpack
    @mock.patch('enterprise.api_client.discovery.CourseCatalogApiServiceClient')
    @mock.patch("enterprise.utils.update_query_parameters", mock.MagicMock(side_effect=side_effect))
    def test_enterprise_customer_catalogs_detail(
            self,
            is_staff,
            is_linked_to_enterprise,
            expected_result,
            mock_catalog_api_client,
    ):
        """
        Make sure the Enterprise Customer's Catalog view correctly returns details about specific catalogs based on
        the content filter.

        Search results should also take into account whether

        * requesting user is staff.
        * requesting user is linked to the EnterpriseCustomer which owns the requested catalog.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(
            uuid=FAKE_UUIDS[0],
            name="test_enterprise"
        )
        factories.EnterpriseCustomerCatalogFactory(
            uuid=FAKE_UUIDS[1],
            enterprise_customer=enterprise_customer
        )
        if not is_staff:
            self.user.is_staff = False
            self.user.save()
        if is_linked_to_enterprise:
            factories.EnterpriseCustomerUserFactory(
                user_id=self.user.id,
                enterprise_customer=enterprise_customer
            )
        mock_catalog_api_client.return_value = mock.Mock(
            get_catalog_results=mock.Mock(
                return_value=fake_catalog_api.FAKE_SEARCH_ALL_RESULTS
            ),
        )
        response = self.client.get(ENTERPRISE_CATALOGS_DETAIL_ENDPOINT)
        response = self.load_json(response.content)

        self.assertDictEqual(response, expected_result)

    @mock.patch('enterprise.api_client.discovery.CourseCatalogApiServiceClient')
    @mock.patch("enterprise.utils.update_query_parameters", mock.MagicMock(side_effect=side_effect))
    def test_enterprise_customer_catalogs_detail_pagination(self, mock_catalog_api_client):
        """
        Verify the EnterpriseCustomerCatalog detail view returns the correct paging URLs.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(
            uuid=FAKE_UUIDS[0],
            name="test_enterprise"
        )
        factories.EnterpriseCustomerCatalogFactory(
            uuid=FAKE_UUIDS[1],
            enterprise_customer=enterprise_customer
        )
        factories.EnterpriseCustomerUserFactory(
            user_id=self.user.id,
            enterprise_customer=enterprise_customer
        )

        mock_catalog_api_client.return_value = mock.Mock(
            get_catalog_results=mock.Mock(
                return_value=fake_catalog_api.FAKE_SEARCH_ALL_RESULTS_WITH_PAGINATION
            ),
        )

        response = self.client.get(ENTERPRISE_CATALOGS_DETAIL_ENDPOINT + '?page=2')
        response = self.load_json(response.content)

        expected_result = fake_enterprise_api.build_fake_enterprise_catalog_detail(
            paginated_content=fake_catalog_api.FAKE_SEARCH_ALL_RESULTS_2,
            previous_url=urljoin('http://testserver', ENTERPRISE_CATALOGS_DETAIL_ENDPOINT) + '?page=1',
            next_url=urljoin('http://testserver/', ENTERPRISE_CATALOGS_DETAIL_ENDPOINT) + '?page=3',
            include_enterprise_context=True,
            add_utm_info=False,
            count=2,
        )

        assert response == expected_result

    @mock.patch('enterprise.api_client.discovery.CourseCatalogApiServiceClient')
    @mock.patch("enterprise.utils.update_query_parameters", mock.MagicMock(side_effect=side_effect))
    def test_enterprise_customer_catalogs_detail_pagination_filtering(self, mock_catalog_api_client):
        """
        Verify the EnterpriseCustomerCatalog detail view returns the correct paging URLs.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(
            uuid=FAKE_UUIDS[0],
            name="test_enterprise"
        )
        factories.EnterpriseCustomerCatalogFactory(
            uuid=FAKE_UUIDS[1],
            enterprise_customer=enterprise_customer
        )
        factories.EnterpriseCustomerUserFactory(
            user_id=self.user.id,
            enterprise_customer=enterprise_customer
        )

        mock_catalog_api_client.return_value = mock.Mock(
            get_catalog_results=mock.Mock(
                return_value=fake_catalog_api.FAKE_SEARCH_ALL_RESULTS_WITH_PAGINATION_1
            ),
        )
        response = self.client.get(ENTERPRISE_CATALOGS_DETAIL_ENDPOINT + '?page=2')
        response = self.load_json(response.content)

        expected_result = fake_enterprise_api.build_fake_enterprise_catalog_detail(
            paginated_content=fake_catalog_api.FAKE_SEARCH_ALL_RESULTS_3,
            previous_url=urljoin('http://testserver', ENTERPRISE_CATALOGS_DETAIL_ENDPOINT) + '?page=1',
            next_url=urljoin('http://testserver/', ENTERPRISE_CATALOGS_DETAIL_ENDPOINT) + '?page=3',
            add_utm_info=False,
            count=5,
        )

        assert response == expected_result

    @ddt.data(
        (False, {'course_run_ids': ['fake1', 'fake2']}, {}),
        (False, {'program_uuids': ['fake1', 'fake2']}, {}),
        (
            True,
            {
                'course_run_ids': [
                    fake_catalog_api.FAKE_COURSE_RUN['key'],
                    fake_catalog_api.FAKE_COURSE_RUN2['key']
                ]
            },
            {
                'results': [
                    fake_catalog_api.FAKE_COURSE_RUN,
                    fake_catalog_api.FAKE_COURSE_RUN2
                ]
            }
        ),
        (
            True,
            {
                'program_uuids': [
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_1['uuid'],
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_2['uuid']
                ]
            },
            {
                'results': [
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_1,
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_2
                ]
            }
        ),
    )
    @ddt.unpack
    @mock.patch('enterprise.api_client.discovery.CourseCatalogApiServiceClient')
    def test_enterprise_catalog_contains_content_items_with_search(self, contains_content_items, query_params,
                                                                   search_results, mock_catalog_api_client):
        """
        Ensure contains_content_items endpoint returns expected result when
        the discovery service's search endpoint is used to determine whether
        or not the catalog contains the given content items.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0])
        factories.EnterpriseCustomerCatalogFactory(
            uuid=FAKE_UUIDS[1],
            enterprise_customer=enterprise_customer,
        )

        mock_catalog_api_client.return_value = mock.Mock(
            get_catalog_results=mock.Mock(return_value=search_results)
        )

        response = self.client.get(ENTERPRISE_CATALOGS_CONTAINS_CONTENT_ENDPOINT + '?' + urlencode(query_params, True))
        response_json = self.load_json(response.content)

        assert response_json['contains_content_items'] == contains_content_items

    @ddt.data(
        (False, {'course_run_ids': ['fake1', 'fake2']}),
        (False, {'program_uuids': ['fake1', 'fake2']}),
        (
            True,
            {
                'course_run_ids': [
                    fake_catalog_api.FAKE_COURSE_RUN['key'],
                    fake_catalog_api.FAKE_COURSE_RUN2['key']
                ]
            },
        ),
        (
            True,
            {
                'program_uuids': [
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_1['uuid'],
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_2['uuid']
                ]
            },
        ),
    )
    @ddt.unpack
    @mock.patch('enterprise.api_client.discovery.CourseCatalogApiServiceClient')
    def test_enterprise_catalog_contains_content_items_without_search(self, contains_content_items, query_params,
                                                                      mock_catalog_api_client):
        """
        Ensure contains_content_items endpoint returns expected result when
        the catalog's content_filter specifies unique keys.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0])
        factories.EnterpriseCustomerCatalogFactory(
            uuid=FAKE_UUIDS[1],
            enterprise_customer=enterprise_customer,
            content_filter={
                'key': [
                    fake_catalog_api.FAKE_COURSE_RUN['key'],
                    fake_catalog_api.FAKE_COURSE_RUN2['key']
                ],
                'uuid': [
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_1['uuid'],
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_2['uuid']
                ]
            }
        )

        mock_catalog_api_client.return_value = mock.Mock(
            get_catalog_results=mock.Mock(return_value={})
        )

        response = self.client.get(ENTERPRISE_CATALOGS_CONTAINS_CONTENT_ENDPOINT + '?' + urlencode(query_params, True))
        response_json = self.load_json(response.content)

        assert response_json['contains_content_items'] == contains_content_items

    def test_enterprise_catalog_contains_content_items_no_query_params(self):
        """
        Ensure contains_content_items endpoint returns error message
        when no query parameters are provided.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0])
        factories.EnterpriseCustomerCatalogFactory(
            uuid=FAKE_UUIDS[1],
            enterprise_customer=enterprise_customer,
        )

        response = self.client.get(ENTERPRISE_CATALOGS_CONTAINS_CONTENT_ENDPOINT)
        response_json = self.load_json(response.content)

        message = response_json[0]
        assert 'program_uuids' in message
        assert 'course_run_ids' in message
        assert response.status_code == 400

    @ddt.data(
        (False, False, False, {}, {'detail': 'Not found.'}),
        (False, True, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (False, True, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (
            False,
            True,
            True,
            fake_catalog_api.FAKE_COURSE_RUN,
            update_course_run_with_enterprise_context(
                fake_catalog_api.FAKE_COURSE_RUN,
                add_utm_info=True,
                enterprise_catalog_uuid=FAKE_UUIDS[1]
            ),
        ),
        (True, False, False, {}, {'detail': 'Not found.'}),
        (True, True, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (True, True, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (
            True,
            True,
            True,
            fake_catalog_api.FAKE_COURSE_RUN,
            update_course_run_with_enterprise_context(
                fake_catalog_api.FAKE_COURSE_RUN,
                add_utm_info=True,
                enterprise_catalog_uuid=FAKE_UUIDS[1]
            ),
        ),
    )
    @ddt.unpack
    @mock.patch('enterprise.models.EnterpriseCatalogApiClient')
    @mock.patch('enterprise.api_client.discovery.CourseCatalogApiServiceClient')
    def test_enterprise_catalog_course_run_detail(
            self,
            is_staff,
            is_linked_to_enterprise,
            is_course_run_in_catalog,
            mocked_course_run,
            expected_result,
            mock_catalog_api_client,
            mock_ent_catalog_api_client
    ):
        """
        The ``course_run`` detail endpoint should return correct results from course discovery,
        with enterprise context in courses.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0], name="test_enterprise")
        factories.EnterpriseCustomerCatalogFactory(
            uuid=FAKE_UUIDS[1],
            enterprise_customer=enterprise_customer,
        )
        if is_staff:
            self.user.is_staff = True
            self.user.save()
        if is_linked_to_enterprise:
            factories.EnterpriseCustomerUserFactory(
                user_id=self.user.id,
                enterprise_customer=enterprise_customer
            )
        search_results = {}
        if is_course_run_in_catalog:
            search_results = {'results': [fake_catalog_api.FAKE_COURSE_RUN]}
        mock_ent_catalog_api_client.return_value.contains_content_items.return_value = is_course_run_in_catalog
        mock_catalog_api_client.return_value = mock.Mock(
            get_catalog_results=mock.Mock(return_value=search_results),
            get_course_run=mock.Mock(return_value=mocked_course_run),
        )
        response = self.client.get(ENTERPRISE_CATALOGS_COURSE_RUN_ENDPOINT)
        response = self.load_json(response.content)

        assert response == expected_result

    @ddt.data(
        (False, False, False, {}, {'detail': 'Not found.'}),
        (False, True, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (False, True, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (
            False,
            True,
            True,
            fake_catalog_api.FAKE_COURSE,
            update_course_with_enterprise_context(
                fake_catalog_api.FAKE_COURSE,
                add_utm_info=True,
                enterprise_catalog_uuid=FAKE_UUIDS[1]
            ),
        ),
        (True, False, False, {}, {'detail': 'Not found.'}),
        (True, True, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (True, True, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (
            True,
            True,
            True,
            fake_catalog_api.FAKE_COURSE,
            update_course_with_enterprise_context(
                fake_catalog_api.FAKE_COURSE,
                add_utm_info=True,
                enterprise_catalog_uuid=FAKE_UUIDS[1]
            ),
        ),
    )
    @ddt.unpack
    @mock.patch('enterprise.models.EnterpriseCatalogApiClient')
    @mock.patch('enterprise.api_client.discovery.CourseCatalogApiServiceClient')
    def test_enterprise_catalog_course_detail(
            self,
            is_staff,
            is_linked_to_enterprise,
            is_course_in_catalog,
            mocked_course,
            expected_result,
            mock_catalog_api_client,
            mock_ent_catalog_api_client
    ):
        """
        The ``course`` detail endpoint should return correct results from course discovery,
        with enterprise context in courses and course runs.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0], name="test_enterprise")
        factories.EnterpriseCustomerCatalogFactory(
            uuid=FAKE_UUIDS[1],
            enterprise_customer=enterprise_customer,
        )
        if is_staff:
            self.user.is_staff = True
            self.user.save()
        if is_linked_to_enterprise:
            factories.EnterpriseCustomerUserFactory(
                user_id=self.user.id,
                enterprise_customer=enterprise_customer
            )
        search_results = {}
        if is_course_in_catalog:
            search_results = {'results': [fake_catalog_api.FAKE_COURSE]}
        mock_ent_catalog_api_client.return_value.contains_content_items.return_value = is_course_in_catalog
        mock_catalog_api_client.return_value = mock.Mock(
            get_catalog_results=mock.Mock(return_value=search_results),
            get_course_details=mock.Mock(return_value=mocked_course),
        )
        response = self.client.get(ENTERPRISE_CATALOGS_COURSE_ENDPOINT)
        response = self.load_json(response.content)

        assert response == expected_result

    @ddt.data(
        (False, False, False, False, {}, {'detail': 'Not found.'}),
        (False, True, False, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (False, True, True, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (
            False,
            True,
            True,
            True,
            fake_catalog_api.FAKE_PROGRAM_RESPONSE1,
            update_program_with_enterprise_context(
                fake_catalog_api.FAKE_PROGRAM_RESPONSE1,
                add_utm_info=True,
                enterprise_catalog_uuid=FAKE_UUIDS[1]
            ),
        ),
        (True, False, False, False, {}, {'detail': 'Not found.'}),
        (True, True, False, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (True, True, True, False, {'detail': 'Not found.'}, {'detail': 'Not found.'}),
        (
            True,
            True,
            True,
            True,
            fake_catalog_api.FAKE_PROGRAM_RESPONSE1,
            update_program_with_enterprise_context(
                fake_catalog_api.FAKE_PROGRAM_RESPONSE1,
                add_utm_info=True,
                enterprise_catalog_uuid=FAKE_UUIDS[1]
            ),
        ),
    )
    @ddt.unpack
    @mock.patch('enterprise.models.EnterpriseCatalogApiClient')
    @mock.patch('enterprise.api_client.discovery.CourseCatalogApiServiceClient')
    def test_enterprise_catalog_program_detail(
            self,
            is_staff,
            is_linked_to_enterprise,
            has_existing_catalog,
            is_program_in_catalog,
            mocked_program,
            expected_result,
            mock_catalog_api_client,
            mock_ent_catalog_api_client
    ):
        """
        The ``programs`` detail endpoint should return correct results from course discovery,
        with enterprise context in courses and course runs.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0], name="test_enterprise")
        factories.EnterpriseCustomerIdentityProviderFactory(
            enterprise_customer=enterprise_customer,
            provider_id='saml-testshib',
        )
        if is_staff:
            self.user.is_staff = True
            self.user.save()
        if is_linked_to_enterprise:
            factories.EnterpriseCustomerUserFactory(
                user_id=self.user.id,
                enterprise_customer=enterprise_customer
            )
        if has_existing_catalog:
            factories.EnterpriseCustomerCatalogFactory(
                uuid=FAKE_UUIDS[1],
                enterprise_customer=enterprise_customer,
            )
        search_results = {}
        if is_program_in_catalog:
            search_results = {'results': [fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_1]}
        mock_ent_catalog_api_client.return_value.contains_content_items.return_value = is_program_in_catalog
        mock_catalog_api_client.return_value = mock.Mock(
            get_catalog_results=mock.Mock(return_value=search_results),
            get_program_by_uuid=mock.Mock(return_value=mocked_program),
        )
        response = self.client.get(ENTERPRISE_CATALOGS_PROGRAM_ENDPOINT)
        response = self.load_json(response.content)

        assert response == expected_result

    @ddt.data(
        (False, {'course_run_ids': ['fake1', 'fake2']}, {}),
        (False, {'program_uuids': ['fake1', 'fake2']}, {}),
        (
            True,
            {
                'course_run_ids': [
                    fake_catalog_api.FAKE_COURSE_RUN['key'],
                    fake_catalog_api.FAKE_COURSE_RUN2['key']
                ]
            },
            {
                'results': [
                    fake_catalog_api.FAKE_COURSE_RUN,
                    fake_catalog_api.FAKE_COURSE_RUN2
                ]
            }
        ),
        (
            True,
            {
                'program_uuids': [
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_1['uuid'],
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_2['uuid']
                ]
            },
            {
                'results': [
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_1,
                    fake_catalog_api.FAKE_SEARCH_ALL_PROGRAM_RESULT_2
                ]
            }
        ),
    )
    @ddt.unpack
    @mock.patch('enterprise.api_client.discovery.CourseCatalogApiServiceClient')
    def test_enterprise_customer_contains_content_items(self, contains_content_items, query_params, search_results,
                                                        mock_catalog_api_client):
        """
        Ensure contains_content_items endpoint returns expected result when query parameters are provided.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0])
        factories.EnterpriseCustomerCatalogFactory(
            uuid=FAKE_UUIDS[1],
            enterprise_customer=enterprise_customer
        )

        mock_catalog_api_client.return_value = mock.Mock(
            get_catalog_results=mock.Mock(return_value=search_results)
        )

        response = self.client.get(ENTERPRISE_CUSTOMER_CONTAINS_CONTENT_ENDPOINT + '?' + urlencode(query_params, True))
        response_json = self.load_json(response.content)

        assert response_json['contains_content_items'] == contains_content_items

    def test_enterprise_customer_contains_content_items_no_catalogs(self):
        """
        Ensure contains_content_items endpoint returns False when the EnterpriseCustomer
        does not have any associated EnterpriseCustomerCatalogs.
        """
        factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0])
        query_params = {'course_run_ids': [
            fake_catalog_api.FAKE_COURSE_RUN['key'],
            fake_catalog_api.FAKE_COURSE_RUN2['key']
        ]}

        response = self.client.get(ENTERPRISE_CUSTOMER_CONTAINS_CONTENT_ENDPOINT + '?' + urlencode(query_params, True))
        response_json = self.load_json(response.content)

        assert response_json['contains_content_items'] is False

    def test_enterprise_customer_contains_content_items_no_query_params(self):
        """
        Ensure contains_content_items endpoint returns an error when no query parameters are provided.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(uuid=FAKE_UUIDS[0])
        factories.EnterpriseCustomerCatalogFactory(
            uuid=FAKE_UUIDS[1],
            enterprise_customer=enterprise_customer,
        )

        response = self.client.get(ENTERPRISE_CUSTOMER_CONTAINS_CONTENT_ENDPOINT)
        response_json = self.load_json(response.content)

        message = response_json[0]
        assert 'program_uuids' in message
        assert 'course_run_ids' in message
        assert response.status_code == 400

    def test_enterprise_customer_course_enrollments_non_list_request(self):
        """
        Test the Enterprise Customer course enrollments detail route with an invalid expected json format.
        """
        factories.EnterpriseCustomerFactory(
            uuid=FAKE_UUIDS[0],
            name="test_enterprise"
        )

        permission = Permission.objects.get(name='Can add Enterprise Customer')
        self.user.user_permissions.add(permission)

        expected_result = {'non_field_errors': ['Expected a list of items but got type "dict".']}

        # Make the call!
        response = self.client.post(
            settings.TEST_SERVER + ENTERPRISE_CUSTOMER_COURSE_ENROLLMENTS_ENDPOINT,
            data=json.dumps({}),
            content_type='application/json',
        )
        response = self.load_json(response.content)

        self.assertDictEqual(response, expected_result)

    def create_course_enrollments_context(
            self,
            user_exists,
            lms_user_id,
            tpa_user_id,
            user_email,
            mock_tpa_client,
            mock_enrollment_client,
            course_enrollment,
            mock_catalog_contains_course,
            course_in_catalog,
            enable_autocohorting=False
    ):
        """
        Set up for tests that call the enterprise customer course enrollments detail route.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory(
            uuid=FAKE_UUIDS[0],
            name="test_enterprise",
            enable_autocohorting=enable_autocohorting
        )
        factories.EnterpriseCustomerIdentityProviderFactory(
            enterprise_customer=enterprise_customer
        )

        permission = Permission.objects.get(name='Can add Enterprise Customer')
        self.user.user_permissions.add(permission)

        user = None
        # Create a preexisting EnterpriseCustomerUser
        if user_exists:
            if lms_user_id:
                user = factories.UserFactory(id=lms_user_id)
            elif tpa_user_id:
                user = factories.UserFactory(username=tpa_user_id)
            elif user_email:
                user = factories.UserFactory(email=user_email)

            factories.EnterpriseCustomerUserFactory(
                user_id=user.id,
                enterprise_customer=enterprise_customer,
            )

        # Set up ThirdPartyAuth API response
        if tpa_user_id:
            mock_tpa_client.return_value = mock.Mock()
            mock_tpa_client.return_value.get_username_from_remote_id = mock.Mock()
            mock_tpa_client.return_value.get_username_from_remote_id.return_value = tpa_user_id

        # Set up EnrollmentAPI responses
        mock_enrollment_client.return_value = mock.Mock(
            get_course_enrollment=mock.Mock(return_value=course_enrollment),
            enroll_user_in_course=mock.Mock()
        )

        # Set up catalog_contains_course response.
        mock_catalog_contains_course.return_value = course_in_catalog

        return enterprise_customer, user

    @ddt.data(
        (
            False,
            False,
            None,
            [{}],
            [{'course_mode': ['This field is required.'], 'course_run_id': ['This field is required.']}],
        ),
        (
            False,
            True,
            None,
            [{'course_mode': 'audit', 'course_run_id': 'course-v1:edX+DemoX+Demo_Course'}],
            [{
                'non_field_errors': [
                    'At least one of the following fields must be specified and map to an EnterpriseCustomerUser: '
                    'lms_user_id, tpa_user_id, user_email'
                ]
            }],
        ),
        (
            False,
            True,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'lms_user_id': 10,
            }],
            [{
                'non_field_errors': [
                    'At least one of the following fields must be specified and map to an EnterpriseCustomerUser: '
                    'lms_user_id, tpa_user_id, user_email'
                ]
            }],
        ),
        (
            False,
            True,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'tpa_user_id': 'abc',
            }],
            [{
                'non_field_errors': [
                    'At least one of the following fields must be specified and map to an EnterpriseCustomerUser: '
                    'lms_user_id, tpa_user_id, user_email'
                ]
            }],
        ),
        (
            True,
            False,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'lms_user_id': 10,
            }],
            [{
                'course_run_id': [
                    'The course run id course-v1:edX+DemoX+Demo_Course is not in the catalog '
                    'for Enterprise Customer test_enterprise'
                ]
            }],
        ),
        (
            False,
            True,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'lms_user_id': 10,
                'tpa_user_id': 'abc',
            }],
            [{
                'non_field_errors': [
                    'At least one of the following fields must be specified and map to an EnterpriseCustomerUser: '
                    'lms_user_id, tpa_user_id, user_email'
                ]
            }],
        ),
        (
            True,
            True,
            {'is_active': True, 'mode': 'verified'},
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'lms_user_id': 10,
            }],
            [{
                'detail': (
                    'The user is already enrolled in the course course-v1:edX+DemoX+Demo_Course '
                    'in verified mode and cannot be enrolled in audit mode'
                )
            }],
        ),
        (
            True,
            True,
            {'is_active': False, 'mode': 'audit'},
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'lms_user_id': 10,
                'cohort': 'masters'
            }],
            [{
                'detail': (
                    'Auto-cohorting is not enabled for this enterprise'
                )
            }],
        ),

    )
    @ddt.unpack
    @mock.patch('enterprise.models.EnterpriseCustomer.catalog_contains_course')
    @mock.patch('enterprise.api.v1.serializers.ThirdPartyAuthApiClient')
    @mock.patch('enterprise.models.EnrollmentApiClient')
    @mock.patch('enterprise.models.utils.track_event', mock.MagicMock())
    def test_enterprise_customer_course_enrollments_detail_errors(
            self,
            user_exists,
            course_in_catalog,
            course_enrollment,
            post_data,
            expected_result,
            mock_enrollment_client,
            mock_tpa_client,
            mock_catalog_contains_course,
    ):
        """
        Test the Enterprise Customer course enrollments detail route error cases.
        """
        payload = post_data[0]
        self.create_course_enrollments_context(
            user_exists,
            payload.get('lms_user_id'),
            payload.get('tpa_user_id'),
            payload.get('user_email'),
            mock_tpa_client,
            mock_enrollment_client,
            course_enrollment,
            mock_catalog_contains_course,
            course_in_catalog
        )

        # Make the call!
        response = self.client.post(
            settings.TEST_SERVER + ENTERPRISE_CUSTOMER_COURSE_ENROLLMENTS_ENDPOINT,
            data=json.dumps(post_data),
            content_type='application/json',
        )
        response = self.load_json(response.content)

        self.assertListEqual(response, expected_result)

    @ddt.data(
        (
            False,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'lms_user_id': 10,
                'tpa_user_id': 'abc',
                'user_email': 'abc@test.com',
            }],
        ),
        (
            True,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'lms_user_id': 10,
            }],
        ),
        (
            True,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'tpa_user_id': 'abc',
            }],
        ),
        (
            True,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'user_email': 'abc@test.com',
            }],
        ),
        (
            True,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'lms_user_id': 10,
                'email_students': True
            }],
        ),
        (
            True,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'lms_user_id': 10,
                'email_students': True,
                'cohort': 'masters',
            }],
        ),
        (
            True,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'user_email': 'foo@bar.com',
                'email_students': True,
                'cohort': 'masters',
            }],
        ),
        (
            True,
            {'is_active': True, 'mode': 'audit'},
            [{
                'course_mode': 'verified',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'lms_user_id': 10,
            }],
        ),
        (
            True,
            {'is_active': False, 'mode': 'audit'},
            [{
                'course_mode': 'verified',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'lms_user_id': 10,
                'is_active': False,
            }],
        ),
        (
            False,
            None,
            [{
                'course_mode': 'audit',
                'course_run_id': 'course-v1:edX+DemoX+Demo_Course',
                'user_email': 'foo@bar.com',
                'is_active': False,
            }],
        ),

    )
    @ddt.unpack
    @mock.patch('enterprise.models.EnterpriseCustomer.catalog_contains_course')
    @mock.patch('enterprise.api.v1.serializers.ThirdPartyAuthApiClient')
    @mock.patch('enterprise.models.EnrollmentApiClient')
    @mock.patch('enterprise.models.EnterpriseCustomer.notify_enrolled_learners')
    @mock.patch('enterprise.models.utils.track_event', mock.MagicMock())
    def test_enterprise_customer_course_enrollments_detail_success(
            self,
            user_exists,
            course_enrollment,
            post_data,
            mock_notify_learners,
            mock_enrollment_client,
            mock_tpa_client,
            mock_catalog_contains_course,
    ):
        """
        Test the Enterprise Customer course enrollments detail route in successful cases.
        """
        payload = post_data[0]
        enterprise_customer, user = self.create_course_enrollments_context(
            user_exists,
            payload.get('lms_user_id'),
            payload.get('tpa_user_id'),
            payload.get('user_email'),
            mock_tpa_client,
            mock_enrollment_client,
            course_enrollment,
            mock_catalog_contains_course,
            True,
            enable_autocohorting=True
        )

        # Make the call!
        response = self.client.post(
            settings.TEST_SERVER + ENTERPRISE_CUSTOMER_COURSE_ENROLLMENTS_ENDPOINT,
            data=json.dumps(post_data),
            content_type='application/json',
        )
        response = self.load_json(response.content)

        expected_response = [{'detail': 'success'}]
        self.assertListEqual(response, expected_response)

        if user_exists:
            if course_enrollment and not course_enrollment['is_active']:
                # check that the user was unenrolled
                assert not EnterpriseCourseEnrollment.objects.filter(
                    enterprise_customer_user__user_id=user.id,
                    course_id=payload.get('course_run_id'),
                ).exists()
                mock_enrollment_client.return_value.unenroll_user_from_course.assert_called_once_with(
                    user.username,
                    payload.get('course_run_id')
                )
            else:
                # If the user already existed, check that the enrollment was performed.
                assert EnterpriseCourseEnrollment.objects.filter(
                    enterprise_customer_user__user_id=user.id,
                    course_id=payload.get('course_run_id'),
                    source=EnterpriseEnrollmentSource.get_source(EnterpriseEnrollmentSource.API)
                ).exists()

                mock_enrollment_client.return_value.get_course_enrollment.assert_called_once_with(
                    user.username, payload.get('course_run_id')
                )
                mock_enrollment_client.return_value.enroll_user_in_course.assert_called_once_with(
                    user.username,
                    payload.get('course_run_id'),
                    payload.get('course_mode'),
                    cohort=payload.get('cohort'),
                )
        elif 'user_email' in payload and payload.get('is_active', True):
            # If a new user given via for user_email, check that the appropriate objects were created.
            pending_ecu = PendingEnterpriseCustomerUser.objects.get(
                enterprise_customer=enterprise_customer,
                user_email=payload.get('user_email'),
            )

            assert pending_ecu is not None
            pending_enrollment = PendingEnrollment.objects.filter(
                user=pending_ecu,
                course_id=payload.get('course_run_id'),
                course_mode=payload.get('course_mode')
            )
            if payload.get('is_active', True):
                assert pending_enrollment[0]
                assert pending_enrollment[0].cohort_name == payload.get('cohort')
                assert pending_enrollment[0].source.slug == EnterpriseEnrollmentSource.API
            else:
                assert not pending_enrollment
            mock_enrollment_client.return_value.get_course_enrollment.assert_not_called()
            mock_enrollment_client.return_value.enroll_user_in_course.assert_not_called()
        elif 'user_email' in payload and not payload.get('is_active', True):
            with raises(PendingEnterpriseCustomerUser.DoesNotExist):
                # No Pending user should have been created in this case.
                PendingEnterpriseCustomerUser.objects.get(
                    user_email=payload.get('user_email'),
                    enterprise_customer=enterprise_customer
                )

        if 'email_students' in payload:
            mock_notify_learners.assert_called_once()
        else:
            mock_notify_learners.assert_not_called()

    @mock.patch('enterprise.models.EnterpriseCustomer.catalog_contains_course')
    @mock.patch('enterprise.api.v1.serializers.ThirdPartyAuthApiClient')
    @mock.patch('enterprise.models.EnrollmentApiClient')
    @mock.patch('enterprise.models.utils.track_event', mock.MagicMock())
    def test_enterprise_customer_course_enrollments_detail_multiple(
            self,
            mock_enrollment_client,
            mock_tpa_client,
            mock_catalog_contains_course,
    ):
        """
        Test the Enterprise Customer course enrollments detail route with multiple enrollments sent.
        """
        tpa_user_id = 'abc'
        new_user_email = 'abc@test.com'
        pending_email = 'foo@bar.com'
        lms_user_id = 10
        course_run_id = 'course-v1:edX+DemoX+Demo_Course'
        payload = [
            {
                'course_mode': 'audit',
                'course_run_id': course_run_id,
                'tpa_user_id': tpa_user_id,
            },
            {
                'course_mode': 'audit',
                'course_run_id': course_run_id,
                'user_email': new_user_email,
            },
            {
                'course_mode': 'audit',
                'course_run_id': course_run_id,
                'lms_user_id': lms_user_id,
            },
            {
                'course_mode': 'audit',
                'course_run_id': course_run_id,
            },
            {
                'course_mode': 'audit',
                'course_run_id': course_run_id,
                'user_email': pending_email,
                'cohort': 'test'
            },
            {
                'course_mode': 'audit',
                'course_run_id': course_run_id,
                'user_email': pending_email,
                'is_active': False,
            },
            {
                'course_mode': 'audit',
                'course_run_id': course_run_id,
                'user_email': pending_email,
                'is_active': True,
            },
            {
                'course_mode': 'audit',
                'course_run_id': course_run_id,
                'user_email': pending_email,
                'is_active': False,
            }
        ]

        expected_response = [
            {'detail': 'success'},
            {'detail': 'success'},
            {
                'detail': (
                    'The user is already enrolled in the course course-v1:edX+DemoX+Demo_Course '
                    'in verified mode and cannot be enrolled in audit mode'
                )
            },
            {
                'non_field_errors': [
                    'At least one of the following fields must be specified and map to an EnterpriseCustomerUser: '
                    'lms_user_id, tpa_user_id, user_email'
                ]
            },
            {'detail': 'success'},
            {'detail': 'success'},
            {'detail': 'success'},
            {'detail': 'success'},
        ]

        enterprise_customer = factories.EnterpriseCustomerFactory(
            uuid=FAKE_UUIDS[0],
            name="test_enterprise"
        )

        permission = Permission.objects.get(name='Can add Enterprise Customer')
        self.user.user_permissions.add(permission)

        # Create a preexisting EnterpriseCustomerUsers
        tpa_user = factories.UserFactory(username=tpa_user_id)
        lms_user = factories.UserFactory(id=lms_user_id)

        factories.EnterpriseCustomerUserFactory(
            user_id=tpa_user.id,
            enterprise_customer=enterprise_customer,
        )

        factories.EnterpriseCustomerUserFactory(
            user_id=lms_user.id,
            enterprise_customer=enterprise_customer,
        )

        # Set up ThirdPartyAuth API response
        mock_tpa_client.return_value = mock.Mock()
        mock_tpa_client.return_value.get_username_from_remote_id = mock.Mock()
        mock_tpa_client.return_value.get_username_from_remote_id.return_value = tpa_user_id

        # Set up EnrollmentAPI responses
        mock_enrollment_client.return_value = mock.Mock(
            get_course_enrollment=mock.Mock(
                side_effect=[None, {'is_active': True, 'mode': 'verified'}]
            ),
            enroll_user_in_course=mock.Mock()
        )

        # Set up catalog_contains_course response.
        mock_catalog_contains_course.return_value = True

        # Make the call!
        response = self.client.post(
            settings.TEST_SERVER + ENTERPRISE_CUSTOMER_COURSE_ENROLLMENTS_ENDPOINT,
            data=json.dumps(payload),
            content_type='application/json',
        )
        response = self.load_json(response.content)

        self.assertListEqual(response, expected_response)
        self.assertFalse(PendingEnrollment.objects.filter(
            user__user_email=pending_email,
            course_id=course_run_id).exists())

    def test_enterprise_customer_catalogs_response_formats(self):
        """
        ``enterprise_catalogs``'s xml and json responses verification.
        """
        response_default = self.client.get('/enterprise/api/v1/enterprise_catalogs/')
        self.assertEqual(response_default['content-type'], 'application/json')

        response_json = self.client.get('/enterprise/api/v1/enterprise_catalogs.json')
        self.assertEqual(response_json['content-type'], 'application/json')

        response_xml = self.client.get('/enterprise/api/v1/enterprise_catalogs.xml')
        self.assertEqual(response_xml['content-type'], 'application/xml; charset=utf-8')

    def test_get_catalog_query(self):
        """
        Test that `CatalogQueryView` returns expected response.
        """
        expected_content_filter = {'partner': 'MushiX'}
        catalog_query = EnterpriseCatalogQuery.objects.create(
            title='Test Catalog Query',
            content_filter=expected_content_filter
        )
        response = self.client.get(
            settings.TEST_SERVER + reverse('enterprise-catalog-query', kwargs={'catalog_query_id': catalog_query.id})
        )
        assert response.status_code == 200
        assert response.json() == expected_content_filter

    def test_get_catalog_query_not_found(self):
        """
        Test that `CatalogQueryView` returns correct response when enterprise catalog query is not found.
        """
        non_existed_id = 100
        response = self.client.get(
            settings.TEST_SERVER + reverse('enterprise-catalog-query', kwargs={'catalog_query_id': non_existed_id})
        )
        assert response.status_code == 404
        response = response.json()
        assert response['detail'] == 'Could not find enterprise catalog query.'

    def test_get_catalog_query_post_method_not_allowed(self):
        """
        Test that `CatalogQueryView` does not allow POST method.
        """
        response = self.client.post(
            settings.TEST_SERVER + reverse('enterprise-catalog-query', kwargs={'catalog_query_id': 1}),
            data=json.dumps({'current_troll_hunter': 'Jim Lake Jr.'}),
            content_type='application/json'
        )
        assert response.status_code == 405
        response = response.json()
        assert response['detail'] == 'Method "POST" not allowed.'

    def test_get_catalog_query_not_staff(self):
        """
        Test that `CatalogQueryView` does not allow non staff users.
        """
        # Creating a non staff user so as to verify the insufficient permission conditions.
        user = factories.UserFactory(username='test_user', is_active=True, is_staff=False)
        user.set_password('test_password')  # pylint: disable=no-member
        user.save()  # pylint: disable=no-member

        client = APIClient()
        client.login(username='test_user', password='test_password')
        response = client.get(
            settings.TEST_SERVER + reverse('enterprise-catalog-query', kwargs={'catalog_query_id': 1})
        )

        assert response.status_code == 403
        response = response.json()
        assert response['detail'] == 'You do not have permission to perform this action.'

    def test_get_catalog_query_not_logged_in(self):
        """
        Test that `CatalogQueryView` only allows logged in users.
        """
        client = APIClient()
        # User is not logged in.
        response = client.get(
            settings.TEST_SERVER + reverse('enterprise-catalog-query', kwargs={'catalog_query_id': 1})
        )
        assert response.status_code == 403
        response = response.json()
        assert response['detail'] == 'Authentication credentials were not provided.'

    @mock.patch('django.core.mail.send_mail')
    @ddt.data(
        (
            # A valid request.
            {
                'email': 'johndoe@unknown.com',
                'enterprise_name': 'Oracle',
                'number_of_codes': '50',
                'notes': 'Here are helping notes',
            },
            {u'email': u'johndoe@unknown.com', u'enterprise_name': u'Oracle', u'number_of_codes': u'50',
             u'notes': u'Here are helping notes'},
            200,
            None,
            True,
            u'johndoe@unknown.com from Oracle has requested 50 additional codes. Please reach out to them.'
            u'\nAdditional Notes:\nHere are helping notes.'.encode("unicode_escape").decode("utf-8")
        ),
        (
            # A valid request without codes
            {
                'email': 'johndoe@unknown.com',
                'enterprise_name': 'Oracle',
                'number_of_codes': None,
                'notes': 'Here are helping notes',
            },
            {u'email': u'johndoe@unknown.com', u'enterprise_name': u'Oracle', u'number_of_codes': None,
             u'notes': u'Here are helping notes'},
            200,
            None,
            True,
            u'johndoe@unknown.com from Oracle has requested additional codes. Please reach out to them.'
            u'\nAdditional Notes:\nHere are helping notes.'.encode("unicode_escape").decode("utf-8")
        ),
        (
            # A valid request without notes
            {
                'email': 'johndoe@unknown.com',
                'enterprise_name': 'Oracle',
                'number_of_codes': '50',
                'notes': None,
            },
            {u'email': u'johndoe@unknown.com', u'enterprise_name': u'Oracle', u'number_of_codes': u'50',
             u'notes': None},
            200,
            None,
            True,
            u'johndoe@unknown.com from Oracle has requested 50 additional codes. Please reach out to them.'
        ),
        (
            # A bad request due to a missing field
            {
                'email': 'johndoe@unknown.com',
                'number_of_codes': '50',
                'notes': 'Here are helping notes',
            },
            {u'error': u'Some required parameter(s) missing: enterprise_name'},
            400,
            None,
            False,
            u'johndoe@unknown.com from Oracle has requested 50 additional codes. Please reach out to them.'
            u'\nAdditional Notes:\nHere are helping notes.'.encode("unicode_escape").decode("utf-8")
        ),
        (
            # Email send issue
            {
                'email': 'johndoe@unknown.com',
                'enterprise_name': 'Oracle',
                'number_of_codes': '50',
                'notes': 'Here are helping notes',
            },
            {u'error': u'Request codes email could not be sent'},
            500,
            SMTPException(),
            True,
            u'johndoe@unknown.com from Oracle has requested 50 additional codes. Please reach out to them.'
            u'\nAdditional Notes:\nHere are helping notes.'.encode("unicode_escape").decode("utf-8")
        )
    )
    @ddt.unpack
    def test_post_request_codes(
            self,
            post_data,
            response_data,
            status_code,
            mock_side_effect,
            mail_attempted,
            expected_email_message,
            mock_send_mail,
    ):
        """
        Ensure endpoint response data and status codes.
        """
        endpoint_name = 'request-codes'
        mock_send_mail.side_effect = mock_side_effect
        response = self.client.post(
            settings.TEST_SERVER + reverse(endpoint_name),
            data=json.dumps(post_data),
            content_type='application/json',
        )
        assert response.status_code == status_code
        response = self.load_json(response.content)

        self.assertDictEqual(response_data, response)
        if mail_attempted:
            mock_send_mail.assert_called_once()
            call_args = (str(w) for w in mock_send_mail.call_args_list)
            self.assertIn(expected_email_message, ''.join(call_args))
        else:
            mock_send_mail.assert_not_called()

    @mock.patch('enterprise.rules.crum.get_current_request')
    @mock.patch('django.core.mail.send_mail', mock.Mock(return_value={'status_code': status.HTTP_200_OK}))
    @ddt.data(
        (False, False, status.HTTP_403_FORBIDDEN),
        (False, True, status.HTTP_200_OK),
        (True, False, status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_post_request_codes_permissions(self, implicit_perm, explicit_perm, expected_status, request_or_stub_mock):
        """
        Test that role base permissions works as expected.
        """
        user = factories.UserFactory(username='test_user', is_active=True, is_staff=False)
        user.set_password('test_password')  # pylint: disable=no-member
        user.save()  # pylint: disable=no-member
        client = APIClient()
        client.login(username='test_user', password='test_password')

        system_wide_role = ENTERPRISE_ADMIN_ROLE

        feature_role_object, __ = EnterpriseFeatureRole.objects.get_or_create(name=ENTERPRISE_DASHBOARD_ADMIN_ROLE)
        EnterpriseFeatureUserRoleAssignment.objects.create(user=user, role=feature_role_object)

        if implicit_perm is False:
            system_wide_role = 'role_with_no_mapped_permissions'

        if explicit_perm is False:
            EnterpriseFeatureUserRoleAssignment.objects.all().delete()

        request_or_stub_mock.return_value = self.get_request_with_jwt_cookie(system_wide_role=system_wide_role)

        post_data = {
            'email': 'johndoe@unknown.com',
            'enterprise_name': 'Oracle',
            'number_of_codes': '50',
        }
        response = client.post(
            settings.TEST_SERVER + reverse('request-codes'),
            data=json.dumps(post_data),
            content_type='application/json',
        )

        assert response.status_code == expected_status

    def test_validate_license_revoke_data_valid_data(self):
        request_data = {
            'user_id': 'anything',
            'enterprise_id': 'something',
        }
        # pylint: disable=protected-access
        self.assertIsNone(LicensedEnterpriseCourseEnrollmentViewSet._validate_license_revoke_data(request_data))

    @ddt.data(
        {},
        {'user_id': 'foo'},
        {'enterprise_id': 'bar'},
    )
    def test_validate_license_revoke_data_invalid_data(self, request_data):
        # pylint: disable=protected-access
        response = LicensedEnterpriseCourseEnrollmentViewSet._validate_license_revoke_data(request_data)
        self.assertEqual(status.HTTP_400_BAD_REQUEST, response.status_code)
        self.assertEqual('user_id and enterprise_id must be provided.', response.data)

    @ddt.data(
        CourseRunProgressStatuses.IN_PROGRESS,
        CourseRunProgressStatuses.UPCOMING,
        CourseRunProgressStatuses.COMPLETED,
        CourseRunProgressStatuses.SAVED_FOR_LATER,
    )
    @mock.patch('enterprise.api.v1.views.get_certificate_for_user')
    @mock.patch('enterprise.api.v1.views.get_course_run_status')
    def test_revoke_has_user_completed_course_run(self, progress_status, mock_course_run_status, mock_cert_for_user):
        enrollment = mock.Mock()
        course_overview = {'id': 'some-course'}

        mock_course_run_status.return_value = progress_status
        expected_result = progress_status == CourseRunProgressStatuses.COMPLETED
        # pylint: disable=protected-access
        actual_result = LicensedEnterpriseCourseEnrollmentViewSet._has_user_completed_course_run(
            enrollment, course_overview
        )
        self.assertEqual(expected_result, actual_result)
        mock_cert_for_user.assert_called_once_with(
            enrollment.enterprise_customer_user.username, 'some-course'
        )
        mock_course_run_status.assert_called_once_with(
            course_overview, mock_cert_for_user.return_value, enrollment
        )

    def test_post_license_revoke_unplugged(self):
        post_data = {
            'user_id': 'bob',
            'enterprise_id': 'bobs-burgers',
        }
        with self.assertRaises(NotConnectedToOpenEdX):
            self.client.post(
                settings.TEST_SERVER + LICENSED_ENTERPISE_COURSE_ENROLLMENTS_REVOKE_ENDPOINT,
                data=post_data,
            )

    def test_post_license_revoke_invalid_data(self):
        with mock.patch('enterprise.api.v1.views.CourseMode'), \
             mock.patch('enterprise.api.v1.views.get_certificate_for_user'), \
             mock.patch('enterprise.api.v1.views.get_course_overviews'):
            post_data = {
                'user_id': 'bob',
            }
            response = self.client.post(
                settings.TEST_SERVER + LICENSED_ENTERPISE_COURSE_ENROLLMENTS_REVOKE_ENDPOINT,
                data=post_data,
            )
            self.assertEqual(status.HTTP_400_BAD_REQUEST, response.status_code)

    def test_post_license_revoke_403(self):
        with mock.patch('enterprise.api.v1.views.CourseMode'), \
             mock.patch('enterprise.api.v1.views.get_certificate_for_user'), \
             mock.patch('enterprise.api.v1.views.get_course_overviews'):

            enterprise_customer = factories.EnterpriseCustomerFactory()
            self.set_jwt_cookie(ENTERPRISE_LEARNER_ROLE, str(enterprise_customer.uuid))
            post_data = {
                'user_id': self.user.id,
                'enterprise_id': enterprise_customer.uuid,
            }
            response = self.client.post(
                settings.TEST_SERVER + LICENSED_ENTERPISE_COURSE_ENROLLMENTS_REVOKE_ENDPOINT,
                data=post_data,
            )
            self.assertEqual(status.HTTP_403_FORBIDDEN, response.status_code)

    @ddt.data(
        {'is_course_completed': False, 'has_audit_mode': True},
        {'is_course_completed': True, 'has_audit_mode': True},
        {'is_course_completed': False, 'has_audit_mode': False},
        {'is_course_completed': True, 'has_audit_mode': False},
    )
    @ddt.unpack
    @mock.patch('enterprise.api.v1.views.CourseMode')
    @mock.patch('enterprise.api.v1.views.EnrollmentApiClient')
    @mock.patch('enterprise.api.v1.views.get_certificate_for_user')
    @mock.patch('enterprise.api.v1.views.get_course_overviews')
    def test_post_license_revoke_all_successes(
            self,
            mock_get_overviews,
            mock_get_certificate,
            mock_enrollment_client,
            mock_course_mode,
            is_course_completed,
            has_audit_mode,
    ):
        mock_course_mode.mode_for_course.return_value = has_audit_mode
        (
            enterprise_customer_user,
            enterprise_course_enrollment,
            licensed_course_enrollment,
        ) = self._revocation_factory_objects()

        mock_get_overviews_response = {
            'id': enterprise_course_enrollment.course_id,
            'pacing': 'instructor',
        }
        # update the mock response based on whether the course enrollment should be considered "completed"
        mock_get_overviews_response.update({
            'has_started': not is_course_completed,
            'has_ended': is_course_completed,
        })

        mock_get_overviews.return_value = [mock_get_overviews_response]
        mock_get_certificate.return_value = {'is_passing': False}
        mock_enrollment_client.return_value = mock.Mock(
            update_course_enrollment_mode_for_user=mock.Mock(),
        )

        post_data = {
            'user_id': self.user.id,
            'enterprise_id': enterprise_customer_user.enterprise_customer.uuid,
        }
        response = self.client.post(
            settings.TEST_SERVER + LICENSED_ENTERPISE_COURSE_ENROLLMENTS_REVOKE_ENDPOINT,
            data=post_data,
        )

        course_id = enterprise_course_enrollment.course_id
        expected_data = {
            course_id: {
                'success': True,
            },
        }
        EnrollmentTerminationStatus = LicensedEnterpriseCourseEnrollmentViewSet.EnrollmentTerminationStatus

        assert response.status_code == status.HTTP_200_OK
        if is_course_completed:
            expected_data[course_id]['message'] = EnrollmentTerminationStatus.COURSE_COMPLETED
        else:
            if has_audit_mode:
                expected_data[course_id]['message'] = EnrollmentTerminationStatus.MOVED_TO_AUDIT
            else:
                expected_data[course_id]['message'] = EnrollmentTerminationStatus.UNENROLLED
        self.assertEqual(expected_data, response.data)

        enterprise_course_enrollment.refresh_from_db()
        licensed_course_enrollment.refresh_from_db()

        # if the course was completed, the enrollment should not have been revoked,
        # and conversely, the enrollment should be revoked if the course was not completed
        revocation_expectation = not is_course_completed
        assert enterprise_course_enrollment.saved_for_later == revocation_expectation
        assert licensed_course_enrollment.is_revoked == revocation_expectation

        if not is_course_completed:
            if has_audit_mode:
                client_instance = mock_enrollment_client.return_value
                client_instance.update_course_enrollment_mode_for_user.assert_called_once_with(
                    username=enterprise_customer_user.username,
                    course_id=enterprise_course_enrollment.course_id,
                    mode=mock_course_mode.AUDIT,
                )
            else:
                client_instance = mock_enrollment_client.return_value
                client_instance.unenroll_user_from_course.assert_called_once_with(
                    username=enterprise_customer_user.username,
                    course_id=enterprise_course_enrollment.course_id,
                )

    @ddt.data(
        {'has_audit_mode': True, 'enrollment_update_error': 'update exception',
         'unenrollment_success': None, 'unenrollment_error': None},
        {'has_audit_mode': False, 'enrollment_update_error': None,
         'unenrollment_success': False, 'unenrollment_error': None},
        {'has_audit_mode': False, 'enrollment_update_error': None,
         'unenrollment_success': None, 'unenrollment_error': 'unenrollment exception'},
    )
    @ddt.unpack
    @mock.patch('enterprise.api.v1.views.CourseMode')
    @mock.patch('enterprise.api.v1.views.EnrollmentApiClient')
    @mock.patch('enterprise.api.v1.views.get_certificate_for_user')
    @mock.patch('enterprise.api.v1.views.get_course_overviews')
    def test_post_license_revoke_all_errors(
            self,
            mock_get_overviews,
            mock_get_certificate,
            mock_enrollment_client,
            mock_course_mode,
            has_audit_mode,
            enrollment_update_error,
            unenrollment_success,
            unenrollment_error,
    ):
        mock_course_mode.mode_for_course.return_value = has_audit_mode
        (
            enterprise_customer_user,
            enterprise_course_enrollment,
            licensed_course_enrollment,
        ) = self._revocation_factory_objects()

        mock_get_overviews_response = {
            'id': enterprise_course_enrollment.course_id,
            'pacing': 'instructor',
        }
        # update the mock response based on whether the course enrollment should be considered "completed"
        # this course is always not completed
        mock_get_overviews_response.update({
            'has_started': True,
            'has_ended': False,
        })

        mock_get_overviews.return_value = [mock_get_overviews_response]
        mock_get_certificate.return_value = {'is_passing': False}
        mock_enrollment_client.return_value = mock.Mock(
            update_course_enrollment_mode_for_user=mock.Mock(),
        )

        client_instance = mock_enrollment_client.return_value
        client_instance.unenroll_user_from_course.return_value = unenrollment_success
        if enrollment_update_error:
            client_instance.update_course_enrollment_mode_for_user.side_effect = Exception(enrollment_update_error)
        if unenrollment_error:
            client_instance.unenroll_user_from_course.side_effect = Exception(unenrollment_error)

        post_data = {
            'user_id': self.user.id,
            'enterprise_id': enterprise_customer_user.enterprise_customer.uuid,
        }
        response = self.client.post(
            settings.TEST_SERVER + LICENSED_ENTERPISE_COURSE_ENROLLMENTS_REVOKE_ENDPOINT,
            data=post_data,
        )

        course_id = enterprise_course_enrollment.course_id
        EnrollmentTerminationStatus = LicensedEnterpriseCourseEnrollmentViewSet.EnrollmentTerminationStatus

        self.assertEqual(status.HTTP_422_UNPROCESSABLE_ENTITY, response.status_code)
        self.assertFalse(response.data[course_id]['success'])
        if enrollment_update_error:
            self.assertIn(enrollment_update_error, response.data[course_id]['message'])
        if unenrollment_success is False:
            self.assertIn(EnrollmentTerminationStatus.UNENROLL_FAILED, response.data[course_id]['message'])
        if unenrollment_error:
            self.assertIn(unenrollment_error, response.data[course_id]['message'])

        enterprise_course_enrollment.refresh_from_db()
        licensed_course_enrollment.refresh_from_db()

        self.assertTrue(enterprise_course_enrollment.saved_for_later)
        self.assertTrue(licensed_course_enrollment.is_revoked)

        if has_audit_mode:
            client_instance = mock_enrollment_client.return_value
            client_instance.update_course_enrollment_mode_for_user.assert_called_once_with(
                username=enterprise_customer_user.username,
                course_id=enterprise_course_enrollment.course_id,
                mode=mock_course_mode.AUDIT,
            )
        else:
            client_instance = mock_enrollment_client.return_value
            client_instance.unenroll_user_from_course.assert_called_once_with(
                username=enterprise_customer_user.username,
                course_id=enterprise_course_enrollment.course_id,
            )

    @ddt.data(
        {
            'body': {},
            'expected_code': 400,
            'expected_body': 'Must include either email or email_csv in request.'
        },
        {
            'body': {
                'email_csv': base64.b64encode(b'bad_column\ntest@example.com').decode(),
                'course_run_key': 'course-v1:edX+DemoX+Demo_Course',
                'course_mode': 'audit',
                'discount': 100,
            },
            'expected_code': 400,
            'expected_body': 'The .csv file must have a column of email addresses,',
        },
        {
            'body': {
                'email': 'newuser@example.com',
                'course_run_key': 'course-v1:edX+DemoX+Demo_Course',
                'course_mode': 'audit',
                'discount': 100,
            },
            'expected_code': 202,
            'expected_body': None,
        },
        {
            'body': {
                'email_csv': base64.b64encode(b'email\ntest@example.com').decode(),
                'course_run_key': 'course-v1:edX+DemoX+Demo_Course',
                'course_mode': 'audit',
                'discount': 100,
            },
            'expected_code': 202,
            'expected_body': None,
        },
    )
    @ddt.unpack
    def test_bulk_enrollment(self, body, expected_code, expected_body):
        """
        Tests the bulk enrollment endpoint at enterprise_learners
        """
        factories.EnterpriseCustomerFactory(
            uuid=FAKE_UUIDS[0],
            name="test_enterprise"
        )

        permission = Permission.objects.get(name='Can add Enterprise Customer')
        self.user.user_permissions.add(permission)

        response = self.client.post(
            settings.TEST_SERVER + ENTERPRISE_CUSTOMER_ENTERPRISE_LEARNERS_ENDPOINT,
            data=json.dumps(body),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, expected_code)
        if expected_body:
            response_content_string = json.dumps(self.load_json(response.content))
            self.assertIn(expected_body, response_content_string)

    @ddt.data(
        {'is_course_completed': False, 'has_audit_mode': True},
        {'is_course_completed': True, 'has_audit_mode': True},
        {'is_course_completed': False, 'has_audit_mode': False},
        {'is_course_completed': True, 'has_audit_mode': False},
    )
    @ddt.unpack
    @mock.patch('enterprise.api.v1.views.CourseMode')
    @mock.patch('enterprise.api.v1.views.get_certificate_for_user')
    @mock.patch('enterprise.api.v1.views.EnrollmentApiClient')
    @mock.patch('enterprise.api.v1.views.get_course_overviews')
    def test_unenroll_expired_licensed_enrollments(
            self,
            mock_get_overviews,
            mock_enrollment_client,
            mock_cert_for_user,
            mock_course_mode,
            is_course_completed,
            has_audit_mode,
    ):
        (
            enterprise_customer_user,
            enterprise_course_enrollment,
            licensed_course_enrollment,
        ) = self._revocation_factory_objects()
        expired_license_uuid = licensed_course_enrollment.license_uuid

        mock_course_mode.mode_for_course.return_value = has_audit_mode
        mock_get_overviews.return_value = [{
            'id': enterprise_course_enrollment.course_id,
            'pacing': 'instructor',
            'has_started': not is_course_completed,
            'has_ended': is_course_completed,
        }]
        mock_cert_for_user.return_value = {'is_passing': False}
        mock_enrollment_client.return_value = mock.Mock(
            update_course_enrollment_mode_for_user=mock.Mock(),
        )

        post_data = {
            'expired_license_uuids': [str(expired_license_uuid), uuid.uuid4()]
        }
        self.client.post(
            settings.TEST_SERVER + EXPIRED_LICENSED_ENTERPRISE_COURSE_ENROLLMENTS_ENDPOINT,
            data=post_data,
            format='json',
        )

        licensed_course_enrollment.refresh_from_db()
        enterprise_course_enrollment.refresh_from_db()

        assert not licensed_course_enrollment.is_revoked

        if not is_course_completed:
            if has_audit_mode:
                client_instance = mock_enrollment_client.return_value
                client_instance.update_course_enrollment_mode_for_user.assert_called_once_with(
                    username=enterprise_customer_user.username,
                    course_id=enterprise_course_enrollment.course_id,
                    mode=mock_course_mode.AUDIT,
                )
            else:
                client_instance = mock_enrollment_client.return_value
                client_instance.unenroll_user_from_course.assert_called_once_with(
                    username=enterprise_customer_user.username,
                    course_id=enterprise_course_enrollment.course_id,
                )
            assert enterprise_course_enrollment.saved_for_later
        else:
            assert not enterprise_course_enrollment.saved_for_later

    def test_unenroll_expired_licensed_enrollments_no_license_ids(self):
        post_data = {
            'user_id': self.user.id,
            'expired_license_uuids': []
        }
        response = self.client.post(
            settings.TEST_SERVER + EXPIRED_LICENSED_ENTERPRISE_COURSE_ENROLLMENTS_ENDPOINT,
            data=post_data,
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def _revocation_factory_objects(self):
        """
        Helper method to provide some testing objects for revocation tests.
        """
        enterprise_customer = factories.EnterpriseCustomerFactory()

        enterprise_customer_user = factories.EnterpriseCustomerUserFactory(
            user_id=self.user.id,
            enterprise_customer=enterprise_customer,
        )
        enterprise_course_enrollment = factories.EnterpriseCourseEnrollmentFactory(
            enterprise_customer_user=enterprise_customer_user,
        )
        licensed_course_enrollment = factories.LicensedEnterpriseCourseEnrollmentFactory(
            enterprise_course_enrollment=enterprise_course_enrollment,
        )

        assert not enterprise_course_enrollment.saved_for_later
        assert not licensed_course_enrollment.is_revoked

        return enterprise_customer_user, enterprise_course_enrollment, licensed_course_enrollment


@ddt.ddt
@mark.django_db
class TestEnterpriseReportingConfigAPIViews(APITest):
    """
    Test Reporting Configuration Views
    """
    def _create_user_and_enterprise_customer(self, username, password):
        """
        Helper method to create the User and Enterprise Customer used in tests.
        """
        user = factories.UserFactory(username=username, is_active=True, is_staff=False)
        user.set_password(password)  # pylint: disable=no-member
        user.save()  # pylint: disable=no-member

        enterprise_customer = factories.EnterpriseCustomerFactory()
        factories.EnterpriseCustomerUserFactory(
            user_id=user.id,
            enterprise_customer=enterprise_customer,
        )

        return user, enterprise_customer

    def _add_feature_role(self, user, feature_role):
        """
        Helper method to create a feature_role and connect it to the User
        """
        feature_role_object, __ = EnterpriseFeatureRole.objects.get_or_create(
            name=feature_role
        )
        EnterpriseFeatureUserRoleAssignment.objects.create(user=user, role=feature_role_object)

    def _assert_config_response(self, expected_data, response_content):
        """
        Helper method to test the response data against the expected JSON data.
        """
        response_content.pop('enterprise_customer')
        for key, value in expected_data.items():
            assert response_content[key] == value

    @mock.patch('enterprise.rules.crum.get_current_request')
    @ddt.data(
        (False, status.HTTP_403_FORBIDDEN),
        (True, status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_reporting_config_retrieve_permissions(self, has_feature_role, expected_status, request_or_stub_mock):
        """
        Tests that the retrieve endpoint respects the Feature Role permissions assigned.
        """
        user, enterprise_customer = self._create_user_and_enterprise_customer('test_user', 'test_password')
        model_item = {
            'enterprise_customer': enterprise_customer,
            'email': 'test@test.com\nfoo@test.com',
            'decrypted_password': 'test_password',
            'decrypted_sftp_password': 'test_password',
            'active': True,
            'delivery_method': 'email',
            'frequency': 'monthly',
            'day_of_month': 1,
            'day_of_week': None,
            'hour_of_day': 1,
            'report_type': 'csv',
            'data_type': 'progress',
        }
        expected_data = {
            'active': True,
            'delivery_method': 'email',
            'frequency': 'monthly',
            'email': ['test@test.com', 'foo@test.com'],
            'day_of_month': 1,
            'day_of_week': None,
            'hour_of_day': 1,
            'report_type': 'csv',
            'data_type': 'progress',
        }
        test_config = factories.EnterpriseCustomerReportingConfigFactory.create(**model_item)

        client = APIClient()
        client.login(username='test_user', password='test_password')

        if has_feature_role:
            self._add_feature_role(user, ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE)
        system_wide_role = ENTERPRISE_ADMIN_ROLE
        request_or_stub_mock.return_value = self.get_request_with_jwt_cookie(system_wide_role=system_wide_role)

        response = client.get(
            '{server}{reverse_url}'.format(
                server=settings.TEST_SERVER,
                reverse_url=reverse(
                    'enterprise-customer-reporting-detail',
                    kwargs={'uuid': str(test_config.uuid)}
                ),
            )
        )

        assert response.status_code == expected_status
        if has_feature_role:
            response_content = self.load_json(response.content)
            assert response_content['enterprise_customer']['uuid'] == str(enterprise_customer.uuid)
            self._assert_config_response(expected_data, response_content)

    @mock.patch('enterprise.rules.crum.get_current_request')
    @ddt.data(
        (False, status.HTTP_403_FORBIDDEN),
        (True, status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_reporting_config_list_permissions(self, has_feature_role, expected_status, request_or_stub_mock):
        """
        Tests that the retrieve endpoint respects the Feature Role permissions assigned.
        """
        user, enterprise_customer = self._create_user_and_enterprise_customer('test_user', 'test_password')
        model_item = {
            'enterprise_customer': enterprise_customer,
            'email': 'test@test.com\nfoo@test.com',
            'decrypted_password': 'test_password',
            'decrypted_sftp_password': 'test_password',
            'active': True,
            'delivery_method': 'email',
            'frequency': 'monthly',
            'day_of_month': 1,
            'day_of_week': None,
            'hour_of_day': 1,
            'report_type': 'csv',
            'data_type': 'progress',
        }
        expected_data = {
            'active': True,
            'delivery_method': 'email',
            'frequency': 'monthly',
            'email': ['test@test.com', 'foo@test.com'],
            'day_of_month': 1,
            'day_of_week': None,
            'hour_of_day': 1,
            'report_type': 'csv',
            'data_type': 'progress',
        }
        factories.EnterpriseCustomerReportingConfigFactory.create_batch(5, **model_item)

        client = APIClient()
        client.login(username='test_user', password='test_password')

        if has_feature_role:
            self._add_feature_role(user, ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE)
        system_wide_role = ENTERPRISE_ADMIN_ROLE
        request_or_stub_mock.return_value = self.get_request_with_jwt_cookie(system_wide_role=system_wide_role)

        response = client.get(
            '{server}{reverse_url}'.format(
                server=settings.TEST_SERVER,
                reverse_url=reverse(
                    'enterprise-customer-reporting-list'
                ),
            )
        )

        assert response.status_code == expected_status
        if has_feature_role:
            results = self.load_json(response.content)['results']
            assert len(results) == 5
            response_content = results[0]
            assert response_content['enterprise_customer']['uuid'] == str(enterprise_customer.uuid)
            self._assert_config_response(expected_data, response_content)

    @mock.patch('enterprise.rules.crum.get_current_request')
    @ddt.data(
        (False, status.HTTP_403_FORBIDDEN),
        (True, status.HTTP_201_CREATED),
    )
    @ddt.unpack
    def test_reporting_config_post_permissions(self, has_feature_role, expected_status, request_or_stub_mock):
        """
        Tests that the POST endpoint respects the Feature Role permissions assigned.
        """
        user, enterprise_customer = self._create_user_and_enterprise_customer('test_user', 'test_password')

        post_data = {
            'active': 'true',
            'delivery_method': 'email',
            'email': ['test@test.com', 'foo@test.com'],
            'encrypted_password': 'testPassword',
            'frequency': 'monthly',
            'day_of_month': 1,
            'day_of_week': 3,
            'hour_of_day': 1,
            'sftp_hostname': 'null',
            'sftp_port': 22,
            'sftp_username': 'test@test.com',
            'sftp_file_path': 'null',
            'data_type': 'progress',
            'report_type': 'csv',
            'pgp_encryption_key': ''
        }
        expected_data = post_data.copy()
        expected_data.update({
            'active': True,
            'encrypted_sftp_password': None,
        })
        expected_data.pop('encrypted_password')
        client = APIClient()
        client.login(username='test_user', password='test_password')

        if has_feature_role:
            self._add_feature_role(user, ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE)
        system_wide_role = ENTERPRISE_ADMIN_ROLE
        request_or_stub_mock.return_value = self.get_request_with_jwt_cookie(system_wide_role=system_wide_role)

        response = client.post(
            '{server}{reverse_url}'.format(
                server=settings.TEST_SERVER,
                reverse_url=reverse(
                    'enterprise-customer-reporting-list'
                ),
            ),
            data=post_data,
            format='json',
        )

        assert response.status_code == expected_status
        if has_feature_role:
            response_content = self.load_json(response.content)
            assert response_content['enterprise_customer']['uuid'] == str(enterprise_customer.uuid)
            assert response_content['encrypted_password'] != post_data['encrypted_password']
            response_content.pop('encrypted_password')
            self._assert_config_response(expected_data, response_content)

    @mock.patch('enterprise.rules.crum.get_current_request')
    @ddt.data(
        (False, status.HTTP_403_FORBIDDEN),
        (True, status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_reporting_config_put_permissions(self, has_feature_role, expected_status, request_or_stub_mock):
        """
        Tests that the PUT endpoint respects the Feature Role permissions assigned.
        """
        user, enterprise_customer = self._create_user_and_enterprise_customer('test_user', 'test_password')
        model_item = {
            'active': True,
            'delivery_method': 'email',
            'day_of_month': 1,
            'day_of_week': None,
            'hour_of_day': 1,
            'enterprise_customer': enterprise_customer,
            'email': 'test@test.com\nfoo@test.com',
            'decrypted_password': 'test_password',
            'decrypted_sftp_password': 'test_password',
            'frequency': 'monthly',
            'report_type': 'csv',
            'data_type': 'progress',
        }
        put_data = {
            'enterprise_customer_id': str(enterprise_customer.uuid),
            'active': 'true',
            'delivery_method': 'email',
            'email': ['test@test.com', 'foo@test.com'],
            'encrypted_password': 'passwordUpdate',
            'frequency': 'monthly',
            'day_of_month': 1,
            'day_of_week': 3,
            'hour_of_day': 1,
            'sftp_hostname': 'null',
            'sftp_port': 22,
            'sftp_username': 'test@test.com',
            'sftp_file_path': 'null',
            'data_type': 'progress',
            'report_type': 'json',
            'pgp_encryption_key': ''
        }
        expected_data = put_data.copy()
        expected_data.pop('encrypted_password')
        expected_data.update({
            'active': True,
        })
        expected_data.pop('enterprise_customer_id')
        test_config = factories.EnterpriseCustomerReportingConfigFactory.create(**model_item)

        client = APIClient()
        client.login(username='test_user', password='test_password')

        if has_feature_role:
            self._add_feature_role(user, ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE)
        system_wide_role = ENTERPRISE_ADMIN_ROLE
        request_or_stub_mock.return_value = self.get_request_with_jwt_cookie(system_wide_role=system_wide_role)

        response = client.put(
            '{server}{reverse_url}'.format(
                server=settings.TEST_SERVER,
                reverse_url=reverse(
                    'enterprise-customer-reporting-detail',
                    kwargs={'uuid': str(test_config.uuid)}
                ),
            ),
            data=put_data,
            format='json',
        )

        assert response.status_code == expected_status
        if has_feature_role:
            response_content = self.load_json(response.content)
            assert response_content['enterprise_customer']['uuid'] == str(enterprise_customer.uuid)
            response_content.pop('enterprise_customer')
            assert response_content['encrypted_password'] is not None
            response_content.pop('encrypted_password')
            for key, value in expected_data.items():
                assert response_content[key] == value

    @mock.patch('enterprise.rules.crum.get_current_request')
    @ddt.data(
        (False, status.HTTP_403_FORBIDDEN),
        (True, status.HTTP_200_OK),
    )
    @ddt.unpack
    def test_reporting_config_patch_permissions(self, has_feature_role, expected_status, request_or_stub_mock):
        """
        Tests that the PATCH endpoint respects the Feature Role permissions assigned.
        """
        user, enterprise_customer = self._create_user_and_enterprise_customer('test_user', 'test_password')
        model_item = {
            'active': True,
            'delivery_method': 'email',
            'day_of_month': 1,
            'day_of_week': None,
            'hour_of_day': 1,
            'enterprise_customer': enterprise_customer,
            'email': 'test@test.com\nfoo@test.com',
            'decrypted_password': 'test_password',
            'decrypted_sftp_password': 'test_password',
            'frequency': 'monthly',
            'report_type': 'csv',
            'data_type': 'progress',
        }
        patch_data = {
            'enterprise_customer_id': str(enterprise_customer.uuid),
            'day_of_month': 4,
            'day_of_week': 1,
            'hour_of_day': 12,
        }
        expected_data = patch_data.copy()
        expected_data.pop('enterprise_customer_id')
        patch_data['encrypted_password'] = 'newPassword'
        patch_data['encrypted_sftp_password'] = 'newSFTPPassword'

        test_config = factories.EnterpriseCustomerReportingConfigFactory.create(**model_item)

        client = APIClient()
        client.login(username='test_user', password='test_password')

        if has_feature_role:
            self._add_feature_role(user, ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE)
        system_wide_role = ENTERPRISE_ADMIN_ROLE
        request_or_stub_mock.return_value = self.get_request_with_jwt_cookie(system_wide_role=system_wide_role)

        response = client.patch(
            '{server}{reverse_url}'.format(
                server=settings.TEST_SERVER,
                reverse_url=reverse(
                    'enterprise-customer-reporting-detail',
                    kwargs={'uuid': str(test_config.uuid)}
                ),
            ),
            data=patch_data,
            format='json',
        )

        assert response.status_code == expected_status
        if has_feature_role:
            response_content = self.load_json(response.content)
            assert response_content['enterprise_customer']['uuid'] == str(enterprise_customer.uuid)
            self._assert_config_response(expected_data, response_content)

    @ddt.data(
        {
            'email': None,
            'error': {'email': ['This field is required']},
            'status_code': status.HTTP_400_BAD_REQUEST
        },
        {
            'email': [],
            'error': {'email': ['This field is required']},
            'status_code': status.HTTP_400_BAD_REQUEST
        },
        {
            'email': ['xyz'],
            'error': {'email': {'0': ['Enter a valid email address.']}},
            'status_code': status.HTTP_400_BAD_REQUEST
        }
    )
    @ddt.unpack
    @mock.patch('enterprise.rules.crum.get_current_request')
    def test_reporting_config_email_delivery(self, request_mock, email, error, status_code):
        """
        Tests that the POST endpoint raises error for email delivery type reporting config with incorrect email field.
        """
        user, __ = self._create_user_and_enterprise_customer('test_user', 'test_password')

        post_data = {
            'active': 'true',
            'delivery_method': 'email',
            'encrypted_password': 'testPassword',
            'frequency': 'monthly',
            'day_of_month': 1,
            'day_of_week': 3,
            'hour_of_day': 1,
            'sftp_hostname': 'null',
            'sftp_port': 22,
            'sftp_username': 'test@test.com',
            'sftp_file_path': 'null',
            'data_type': 'progress',
            'report_type': 'csv',
            'pgp_encryption_key': '',
        }
        if email is not None:
            post_data['email'] = email

        client = APIClient()
        client.login(username='test_user', password='test_password')
        self._add_feature_role(user, ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE)
        request_mock.return_value = self.get_request_with_jwt_cookie(system_wide_role=ENTERPRISE_ADMIN_ROLE)

        response = client.post(
            '{server}{reverse_url}'.format(
                server=settings.TEST_SERVER,
                reverse_url=reverse(
                    'enterprise-customer-reporting-list'
                ),
            ),
            data=post_data,
            format='json',
        )

        assert response.status_code == status_code
        if error:
            assert response.json() == error

    @mock.patch('enterprise.rules.crum.get_current_request')
    def test_reporting_config_sftp_delivery(self, request_mock):
        """
        Tests that the POST endpoint works as expected for sftp delivery type reporting config without email field.
        """
        user, __ = self._create_user_and_enterprise_customer('test_user', 'test_password')

        post_data = {
            'active': 'true',
            'delivery_method': 'sftp',
            'encrypted_password': 'testPassword',
            'frequency': 'monthly',
            'day_of_month': 1,
            'day_of_week': 3,
            'hour_of_day': 1,
            'sftp_hostname': 'null',
            'sftp_port': 22,
            'sftp_username': 'test@test.com',
            'sftp_file_path': 'null',
            'data_type': 'progress',
            'report_type': 'csv',
            'pgp_encryption_key': ''
        }

        client = APIClient()
        client.login(username='test_user', password='test_password')
        self._add_feature_role(user, ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE)
        request_mock.return_value = self.get_request_with_jwt_cookie(system_wide_role=ENTERPRISE_ADMIN_ROLE)

        response = client.post(
            '{server}{reverse_url}'.format(
                server=settings.TEST_SERVER,
                reverse_url=reverse(
                    'enterprise-customer-reporting-list'
                ),
            ),
            data=post_data,
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED

    @mock.patch('enterprise.rules.crum.get_current_request')
    def test_reporting_config_patch_with_email_delivery(self, request_or_stub_mock):
        """
        Tests that the PATCH endpoint respects the Feature Role permissions assigned.
        """
        user, enterprise_customer = self._create_user_and_enterprise_customer('test_user', 'test_password')
        model_item = {
            'active': True,
            'delivery_method': 'email',
            'day_of_month': 1,
            'day_of_week': None,
            'hour_of_day': 1,
            'enterprise_customer': enterprise_customer,
            'email': 'test@test.com\nfoo@test.com',
            'decrypted_password': 'test_password',
            'decrypted_sftp_password': 'test_password',
            'frequency': 'monthly',
            'report_type': 'csv',
            'data_type': 'progress',
        }
        patch_data = {
            'enterprise_customer_id': str(enterprise_customer.uuid),
            'email': [],
        }

        test_config = factories.EnterpriseCustomerReportingConfigFactory.create(**model_item)

        client = APIClient()
        client.login(username='test_user', password='test_password')

        self._add_feature_role(user, ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE)
        request_or_stub_mock.return_value = self.get_request_with_jwt_cookie(system_wide_role=ENTERPRISE_ADMIN_ROLE)

        response = client.patch(
            '{server}{reverse_url}'.format(
                server=settings.TEST_SERVER,
                reverse_url=reverse(
                    'enterprise-customer-reporting-detail',
                    kwargs={'uuid': str(test_config.uuid)}
                ),
            ),
            data=patch_data,
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
