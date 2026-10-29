import unittest

import httpx
from openai import APIConnectionError, APITimeoutError, BadRequestError, RateLimitError

from image_api_errors import image_api_error


class ImageApiErrorTests(unittest.TestCase):
    def api_error(self, cls, status, body):
        response = httpx.Response(status, request=httpx.Request('POST', 'https://api.openai.com/v1/images/edits'),
                                  headers={'x-request-id': 'req_example', 'retry-after': '30'})
        return cls('Private upstream text', response=response, body=body)

    def test_quota_is_actionable_and_not_presented_as_transient_throttling(self):
        error = self.api_error(RateLimitError, 429, {'error': {'type': 'insufficient_quota', 'code': 'billing_not_active'}})
        status, body = image_api_error(error)
        self.assertEqual(status, 429)
        self.assertEqual(body['code'], 'billing_not_active')
        self.assertEqual(body['type'], 'insufficient_quota')
        self.assertEqual(body['error'], 'your OpenAI account is out of credit')
        self.assertNotIn('pause', body['error'])
        self.assertNotIn('retry_after', body)
        self.assertEqual(body['request_id'], 'req_example')

    def test_real_throttling_preserves_retry_guidance(self):
        error = self.api_error(RateLimitError, 429, {'code': 'rate_limit_exceeded', 'type': 'rate_limit_error'})
        self.assertIn('retried once', image_api_error(error)[1]['error'])
        self.assertIn('retry', image_api_error(error)[1]['error'].lower())

    def test_photo_rejection_is_visible_without_exposing_raw_response(self):
        error = self.api_error(BadRequestError, 400, {'code': 'content_policy_violation',
            'message': 'Private original prompt and credential-shaped data'})
        status, body = image_api_error(error)
        self.assertEqual(status, 400)
        self.assertIn('safety review', body['error'])
        self.assertIn('content_policy_violation', body['error'])
        self.assertIn('request req_example', body['error'])
        self.assertNotIn('Private', str(body))

    def test_rejected_parameter_is_identified_but_raw_text_is_never_shown(self):
        error = self.api_error(BadRequestError, 400, {'code': 'unsupported_parameter',
            'param': 'quality', 'message': 'Private upstream text'})
        self.assertIn('quality setting', image_api_error(error)[1]['error'])
        self.assertNotIn('Private', str(image_api_error(error)))

    def test_network_and_timeout_need_different_retry_guidance(self):
        request = httpx.Request('POST', 'https://api.openai.com/v1/images/edits')
        network = image_api_error(APIConnectionError(request=request))[1]['error']
        timeout = image_api_error(APITimeoutError(request=request))[1]['error']
        self.assertIn('could not connect', network)
        self.assertIn('completion is unknown', timeout)
        self.assertNotEqual(network, timeout)

    def test_unknown_exception_does_not_echo_secrets_or_local_paths(self):
        body = image_api_error(RuntimeError('Private upstream text C:/private/image.png'))[1]
        self.assertNotIn('Private', str(body))
        self.assertNotIn('C:/private', str(body))


if __name__ == '__main__':
    unittest.main()
