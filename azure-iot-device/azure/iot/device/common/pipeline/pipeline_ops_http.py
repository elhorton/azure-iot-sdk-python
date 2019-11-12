# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
from . import PipelineOperation


class SetHTTPConnectionArgsOperation(PipelineOperation):
    """
    A PipelineOperation object which contains arguments used to connect to a server using the HTTP protocol.

    This operation is in the group of HTTP operations because its attributes are very specific to the HTTP protocol.
    """

    def __init__(self, hostname, callback, ca_cert=None, client_cert=None, sas_token=None):
        """
        Initializer for SetHTTPConnectionArgsOperation objects.
        TODO: See if I need to delete these. Also look into the HTTP Transport and http.client
        to see if there is a way to configure a ca_cert and sas token.... Oh I definitely need a sas token.
        :param str client_id: The client identifier to use when connecting to the HTTP server
        :param str hostname: The hostname of the HTTP server we will eventually connect to
        :param str ca_cert: (Optional) The CA certificate to use if the HTTP server that we're going to
          connect to uses server-side TLS
        :param X509 client_cert: (Optional) The x509 object containing a client certificate and key used to connect
          to the HTTP service
        :param str sas_token: The token string which will be used to authenticate with the service
        :param Function callback: The function that gets called when this operation is complete or has failed.
          The callback function must accept A PipelineOperation object which indicates the specific operation which
          has completed or failed.
        """
        super(SetHTTPConnectionArgsOperation, self).__init__(callback=callback)
        self.hostname = hostname
        self.ca_cert = ca_cert
        self.client_cert = client_cert
        self.sas_token = sas_token


class HTTPPublishOperation(PipelineOperation):
    """
    A PipelineOperation object which contains arguments used to publish a specific payload on a specific topic using the HTTP protocol.

    This operation is in the group of HTTP operations because its attributes are very specific to the HTTP protocol.
    """

    def __init__(self, topic, payload, callback):
        """
        Initializer for HTTPPublishOperation objects.

        :param str topic: The name of the topic to publish to
        :param str payload: The payload to publish
        :param Function callback: The function that gets called when this operation is complete or has failed.
          The callback function must accept A PipelineOperation object which indicates the specific operation which
          has completed or failed.
        """
        super(HTTPPublishOperation, self).__init__(callback=callback)
        self.topic = topic
        self.payload = payload
        self.needs_connection = True
        self.retry_timer = None


class HTTPSubscribeOperation(PipelineOperation):
    """
    A PipelineOperation object which contains arguments used to subscribe to a specific HTTP topic using the HTTP protocol.

    This operation is in the group of HTTP operations because its attributes are very specific to the HTTP protocol.
    """

    def __init__(self, topic, callback):
        """
        Initializer for HTTPSubscribeOperation objects.

        :param str topic: The name of the topic to subscribe to
        :param Function callback: The function that gets called when this operation is complete or has failed.
          The callback function must accept A PipelineOperation object which indicates the specific operation which
          has completed or failed.
        """
        super(HTTPSubscribeOperation, self).__init__(callback=callback)
        self.topic = topic
        self.needs_connection = True
        self.timeout_timer = None
        self.retry_timer = None


class HTTPUnsubscribeOperation(PipelineOperation):
    """
    A PipelineOperation object which contains arguments used to unsubscribe from a specific HTTP topic using the HTTP protocol.

    This operation is in the group of HTTP operations because its attributes are very specific to the HTTP protocol.
    """

    def __init__(self, topic, callback):
        """
        Initializer for HTTPUnsubscribeOperation objects.

        :param str topic: The name of the topic to unsubscribe from
        :param Function callback: The function that gets called when this operation is complete or has failed.
          The callback function must accept A PipelineOperation object which indicates the specific operation which
          has completed or failed.
        """
        super(HTTPUnsubscribeOperation, self).__init__(callback=callback)
        self.topic = topic
        self.needs_connection = True
        self.timeout_timer = None
        self.retry_timer = None