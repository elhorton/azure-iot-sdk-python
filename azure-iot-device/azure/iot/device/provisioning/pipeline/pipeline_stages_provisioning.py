# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

from azure.iot.device.common.pipeline import pipeline_ops_base, pipeline_thread
from azure.iot.device.common.pipeline.pipeline_stages_base import PipelineStage
from . import pipeline_ops_provisioning
from azure.iot.device import exceptions
from azure.iot.device.provisioning.pipeline import constant
from azure.iot.device.provisioning.models.registration_result import (
    RegistrationResult,
    RegistrationState,
)
import logging
import weakref
import json
from threading import Timer
import time
from .mqtt_topic import get_optional_element

logger = logging.getLogger(__name__)


class UseSecurityClientStage(PipelineStage):
    """
    PipelineStage which extracts relevant SecurityClient values for a new
    SetProvisioningClientConnectionArgsOperation.

    All other operations are passed down.
    """

    @pipeline_thread.runs_on_pipeline_thread
    def _run_op(self, op):
        if isinstance(op, pipeline_ops_provisioning.SetSymmetricKeySecurityClientOperation):

            security_client = op.security_client
            worker_op = op.spawn_worker_op(
                worker_op_type=pipeline_ops_provisioning.SetProvisioningClientConnectionArgsOperation,
                provisioning_host=security_client.provisioning_host,
                registration_id=security_client.registration_id,
                id_scope=security_client.id_scope,
                sas_token=security_client.get_current_sas_token(),
            )
            self.send_op_down(worker_op)

        elif isinstance(op, pipeline_ops_provisioning.SetX509SecurityClientOperation):
            security_client = op.security_client
            worker_op = op.spawn_worker_op(
                worker_op_type=pipeline_ops_provisioning.SetProvisioningClientConnectionArgsOperation,
                provisioning_host=security_client.provisioning_host,
                registration_id=security_client.registration_id,
                id_scope=security_client.id_scope,
                client_cert=security_client.get_x509_certificate(),
            )
            self.send_op_down(worker_op)

        else:
            super(UseSecurityClientStage, self)._run_op(op)


class CommonProvisioningStage(PipelineStage):
    """
    This is a super stage that the RegistrationStage and PollingStatusStage of
    provisioning would both use. It  some common functions like decoding response
    and retrieving error, registration status, operation id and forming a complete result.
    """

    @pipeline_thread.runs_on_pipeline_thread
    def _clear_timeout_timer(self, op, error):
        """
        Clearing timer for provisioning operations (Register and PollStatus)
        when they respond back from service.
        """
        if op.provisioning_timeout_timer:
            logger.debug("{}({}): Cancelling provisioning timeout timer".format(self.name, op.name))
            op.provisioning_timeout_timer.cancel()
            op.provisioning_timeout_timer = None

    @staticmethod
    def _get_error(provisioning_op, error):
        if error:
            return error
        elif 300 <= provisioning_op.status_code < 429:
            logger.error(
                "Received error with status code {status_code} for {prov_op_name} request operation".format(
                    prov_op_name=provisioning_op.request_type,
                    status_code=provisioning_op.status_code,
                )
            )
            logger.error("response body: {}".format(provisioning_op.response_body))
            return exceptions.ServiceError(
                "{prov_op_name} request returned a service error status code {status_code}".format(
                    prov_op_name=provisioning_op.request_type,
                    status_code=provisioning_op.status_code,
                )
            )
        else:
            return None

    @staticmethod
    def _decode_response(provisioning_op):
        return json.loads(provisioning_op.response_body.decode("utf-8"))

    @staticmethod
    def _get_registration_status(decoded_response):
        return get_optional_element(decoded_response, "status")

    @staticmethod
    def _get_operation_id(decoded_response):
        return get_optional_element(decoded_response, "operationId")

    @staticmethod
    def _form_complete_result(operation_id, decoded_response, status):
        """
        Create the registration result from the complete decoded json response for details regarding the registration process.
        """
        decoded_state = (get_optional_element(decoded_response, "registrationState"),)
        registration_state = None
        if decoded_state is not None:
            registration_state = RegistrationState(
                device_id=get_optional_element(decoded_state[0], "deviceId"),
                assigned_hub=get_optional_element(decoded_state[0], "assignedHub"),
                sub_status=get_optional_element(decoded_state[0], "substatus"),
                created_date_time=get_optional_element(decoded_state[0], "createdDateTimeUtc"),
                last_update_date_time=get_optional_element(
                    decoded_state[0], "lastUpdatedDateTimeUtc"
                ),
                etag=get_optional_element(decoded_state[0], "etag"),
                payload=get_optional_element(decoded_state[0], "payload"),
            )

        registration_result = RegistrationResult(
            operation_id=operation_id, status=status, registration_state=registration_state
        )
        return registration_result


class PollingStatusStage(CommonProvisioningStage):
    """
    This stage is responsible for sending the query request once initial response
    is received from the registration response.
    Upon the receipt of the response this stage decides whether
    to send another query request or complete the procedure.
    """

    @pipeline_thread.runs_on_pipeline_thread
    def _run_op(self, op):
        if isinstance(op, pipeline_ops_provisioning.PollStatusOperation):
            query_status_op = op

            self_weakref = weakref.ref(self)

            @pipeline_thread.invoke_on_pipeline_thread_nowait
            def query_timeout():
                this = self_weakref()
                logger.info(
                    "{stage_name}({op_name}): returning timeout error".format(
                        stage_name=this.name, op_name=op.name
                    )
                )
                query_status_op.complete(
                    error=(
                        exceptions.ServiceError(
                            "Operation timed out before provisioning service could respond for PollStatus operation".format()
                        )
                    )
                )

            logger.debug("{}({}): Creating provisioning timeout timer".format(self.name, op.name))
            query_status_op.provisioning_timeout_timer = Timer(
                constant.DEFAULT_TIMEOUT_INTERVAL, query_timeout
            )
            query_status_op.provisioning_timeout_timer.start()

            def on_query_response(op, error):
                self._clear_timeout_timer(query_status_op, error)
                logger.debug(
                    "{stage_name}({op_name}): Received response with status code {status_code} for PollStatusOperation with operation id {oper_id}".format(
                        stage_name=self.name,
                        op_name=op.name,
                        status_code=op.status_code,
                        oper_id=op.query_params["operation_id"],
                    )
                )

                # This could be an error that has been reported to this stage Or this
                # could be an error because the service responded with status code 300
                error = CommonProvisioningStage._get_error(op, error=error)

                if not error:
                    success_status_code = op.status_code
                    polling_interval = (
                        int(op.retry_after, 10)
                        if op.retry_after is not None
                        else constant.DEFAULT_POLLING_INTERVAL
                    )
                    decoded_response = CommonProvisioningStage._decode_response(op)
                    operation_id = CommonProvisioningStage._get_operation_id(decoded_response)
                    registration_status = CommonProvisioningStage._get_registration_status(
                        decoded_response
                    )

                    # retry after or assigning scenario
                    if success_status_code >= 429 or registration_status == "assigning":

                        self_weakref = weakref.ref(self)

                        @pipeline_thread.invoke_on_pipeline_thread_nowait
                        def do_polling():
                            this = self_weakref()
                            logger.info(
                                "{stage_name}({op_name}): retrying".format(
                                    stage_name=this.name, op_name=op.name
                                )
                            )
                            query_status_op.polling_timer.cancel()
                            query_status_op.polling_timer = None
                            query_status_op.completed = False
                            this.run_op(query_status_op)

                        logger.info(
                            "{stage_name}({op_name}): Op needs retry with interval {interval} because of {error}. Setting timer.".format(
                                stage_name=self.name,
                                op_name=op.name,
                                interval=polling_interval,
                                error=error,
                            )
                        )

                        logger.debug("{}({}): Creating polling timer".format(self.name, op.name))
                        query_status_op.polling_timer = Timer(polling_interval, do_polling)
                        query_status_op.polling_timer.start()

                    # Service success scenario
                    else:
                        registration_status = CommonProvisioningStage._get_registration_status(
                            decoded_response
                        )
                        if registration_status == "assigned" or registration_status == "failed":
                            # process complete response here
                            complete_registration_result = CommonProvisioningStage._form_complete_result(
                                operation_id=operation_id,
                                decoded_response=decoded_response,
                                status=registration_status,
                            )
                            query_status_op.registration_result = complete_registration_result

                            if registration_status == "failed":
                                error = exceptions.ServiceError(
                                    "Query Status operation returned a failed registration status  with a status code of {status_code}".format(
                                        status_code=success_status_code
                                    )
                                )
                        else:
                            error = exceptions.ServiceError(
                                "Query Status Operation encountered an invalid registration status {status} with a status code of {status_code}".format(
                                    status=registration_status, status_code=success_status_code
                                )
                            )

                        query_status_op.complete(error=error)

                else:
                    query_status_op.complete(error=error)

            self.send_op_down(
                pipeline_ops_base.RequestAndResponseOperation(
                    request_type=constant.QUERY,
                    method="GET",
                    resource_location="/",
                    query_params={"operation_id": query_status_op.operation_id},
                    request_body=query_status_op.request_payload,
                    callback=on_query_response,
                )
            )

        else:
            super(PollingStatusStage, self)._run_op(op)


class RegistrationStage(CommonProvisioningStage):
    """
    This is the first stage that decides converts a registration request
    into a normal request and response operation.
    Upon the receipt of the response this stage decides whether
    to send another registration request or send a query request.
    Depending on the status and result of the response
    this stage may also complete the registration process.
    """

    @pipeline_thread.runs_on_pipeline_thread
    def _run_op(self, op):
        if isinstance(op, pipeline_ops_provisioning.RegisterOperation):
            initial_register_op = op
            self_weakref = weakref.ref(self)

            @pipeline_thread.invoke_on_pipeline_thread_nowait
            def register_timeout():
                this = self_weakref()
                logger.info(
                    "{stage_name}({op_name}): returning timeout error".format(
                        stage_name=this.name, op_name=op.name
                    )
                )
                initial_register_op.complete(
                    error=(
                        exceptions.ServiceError(
                            "Operation timed out before provisioning service could respond for Register operation".format()
                        )
                    )
                )

            logger.debug("{}({}): Creating provisioning timeout timer".format(self.name, op.name))
            initial_register_op.provisioning_timeout_timer = Timer(
                constant.DEFAULT_TIMEOUT_INTERVAL, register_timeout
            )
            initial_register_op.provisioning_timeout_timer.start()

            def on_registration_response(op, error):
                self._clear_timeout_timer(initial_register_op, error)
                logger.debug(
                    "{stage_name}({op_name}): Received response with status code {status_code} for RegisterOperation".format(
                        stage_name=self.name, op_name=op.name, status_code=op.status_code
                    )
                )

                # This could be an error that has been reported to this stage Or this
                # could be an error because the service responded with status code >=300
                error = CommonProvisioningStage._get_error(op, error=error)

                if not error:
                    success_status_code = op.status_code
                    retry_interval = (
                        int(op.retry_after, 10)
                        if op.retry_after is not None
                        else constant.DEFAULT_POLLING_INTERVAL
                    )
                    decoded_response = CommonProvisioningStage._decode_response(op)
                    operation_id = CommonProvisioningStage._get_operation_id(decoded_response)

                    # retry after scenario
                    if success_status_code >= 429:

                        self_weakref = weakref.ref(self)

                        @pipeline_thread.invoke_on_pipeline_thread_nowait
                        def do_retry_after():
                            this = self_weakref()
                            logger.info(
                                "{stage_name}({op_name}): retrying".format(
                                    stage_name=this.name, op_name=op.name
                                )
                            )
                            initial_register_op.retry_after_timer.cancel()
                            initial_register_op.retry_after_timer = None
                            initial_register_op.completed = False
                            this.run_op(initial_register_op)

                        logger.warning(
                            "{stage_name}({op_name}): Op needs retry with interval {interval} because of {error}.  Setting timer.".format(
                                stage_name=self.name,
                                op_name=op.name,
                                interval=retry_interval,
                                error=error,
                            )
                        )

                        logger.debug("{}({}): Creating retry timer".format(self.name, op.name))
                        initial_register_op.retry_after_timer = Timer(
                            retry_interval, do_retry_after
                        )
                        initial_register_op.retry_after_timer.start()

                    # Service success scenario
                    else:
                        registration_status = CommonProvisioningStage._get_registration_status(
                            decoded_response
                        )
                        if registration_status == "assigned" or registration_status == "failed":
                            # process complete response here
                            # TODO Is there an response when service returns "failed" ?
                            complete_registration_result = CommonProvisioningStage._form_complete_result(
                                operation_id=operation_id,
                                decoded_response=decoded_response,
                                status=registration_status,
                            )
                            initial_register_op.registration_result = complete_registration_result
                            if registration_status == "failed":
                                error = exceptions.ServiceError(
                                    "Registration operation returned failed registration status  with a status code of {status_code}".format(
                                        status_code=success_status_code
                                    )
                                )
                            # complete the original op in both cases
                            initial_register_op.complete(error=error)
                        elif registration_status == "assigning":

                            self_weakref = weakref.ref(self)

                            def copy_result_to_original_op(op, error):
                                logger.debug(
                                    "Copying registration result from Query Status Op to Registration Op"
                                )
                                initial_register_op.registration_result = op.registration_result
                                initial_register_op.error = error

                            @pipeline_thread.invoke_on_pipeline_thread_nowait
                            def do_query_after_interval():
                                this = self_weakref()
                                initial_register_op.polling_timer.cancel()
                                initial_register_op.polling_timer = None

                                logger.info(
                                    "{stage_name}({op_name}): polling".format(
                                        stage_name=this.name, op_name=op.name
                                    )
                                )

                                query_worker_op = initial_register_op.spawn_worker_op(
                                    worker_op_type=pipeline_ops_provisioning.PollStatusOperation,
                                    request_payload=" ",
                                    operation_id=operation_id,
                                    callback=copy_result_to_original_op,
                                )

                                self.send_op_down(query_worker_op)

                            logger.warning(
                                "{stage_name}({op_name}): Op will transition into polling after interval {interval}.  Setting timer.".format(
                                    stage_name=self.name,
                                    op_name=op.name,
                                    interval=constant.DEFAULT_POLLING_INTERVAL,
                                )
                            )

                            logger.debug(
                                "{}({}): Creating polling timer".format(self.name, op.name)
                            )
                            initial_register_op.polling_timer = Timer(
                                constant.DEFAULT_POLLING_INTERVAL, do_query_after_interval
                            )
                            initial_register_op.polling_timer.start()

                        else:
                            error = exceptions.ServiceError(
                                "Registration Request encountered an invalid registration status {status} with a status code of {status_code}".format(
                                    status=registration_status, status_code=success_status_code
                                )
                            )
                            initial_register_op.complete(error=error)

                else:
                    initial_register_op.complete(error=error)

            registration_payload = DeviceRegistrationPayload(
                registration_id=initial_register_op.registration_id,
                custom_payload=initial_register_op.request_payload,
            )
            self.send_op_down(
                pipeline_ops_base.RequestAndResponseOperation(
                    request_type=constant.REGISTER,
                    method="PUT",
                    resource_location="/",
                    request_body=registration_payload.get_json_string(),
                    callback=on_registration_response,
                )
            )

        else:
            super(RegistrationStage, self)._run_op(op)


class DeviceRegistrationPayload(object):
    """
    The class representing the payload that needs to be sent to the service.
    """

    def __init__(self, registration_id, custom_payload=None):
        # This is not a convention to name variables in python but the
        # DPS service spec needs the name to be exact for it to work
        self.registrationId = registration_id
        self.payload = custom_payload

    def get_json_string(self):
        return json.dumps(self, default=lambda o: o.__dict__, sort_keys=True)
