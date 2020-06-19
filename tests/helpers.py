# -*- coding: utf-8 -*-
"""
helpers
~~~~~~~

This module contains helpers for the nio tests.
"""

import os
from random import choice
from string import ascii_letters, ascii_uppercase

from faker import Faker
from faker.providers import BaseProvider
from hpack.hpack import Encoder
from hyperframe.frame import (AltSvcFrame, ContinuationFrame, DataFrame,
                              GoAwayFrame, HeadersFrame, PingFrame,
                              PriorityFrame, PushPromiseFrame, RstStreamFrame,
                              SettingsFrame, WindowUpdateFrame)

from nio.crypto import (
    OlmAccount,
    OlmDevice,
    UserIdentity,
    MasterPubkeys,
    SelfSigningPubkeys,
    UserSigningPubkeys
)
from olm import PkSigning
from nio.store import Ed25519Key
from nio import Api

SAMPLE_SETTINGS = {
    SettingsFrame.HEADER_TABLE_SIZE: 4096,
    SettingsFrame.ENABLE_PUSH: 1,
    SettingsFrame.MAX_CONCURRENT_STREAMS: 2,
}


faker = Faker()


class Provider(BaseProvider):
    def mx_id(self):
        return "@{}:{}".format(faker.user_name(), faker.hostname())

    def avatar_url(self):
        return "mxc://{}/{}#auto".format(
            faker.hostname(),
            "".join(choice(ascii_letters) for i in range(24))
        )

    def device_id(self):
        return "".join(choice(ascii_uppercase) for i in range(10))

    def cross_signing_identity(self):
        user_id = faker.mx_id()

        master = PkSigning(PkSigning.generate_seed())
        self_signing = PkSigning(PkSigning.generate_seed())
        user = PkSigning(PkSigning.generate_seed())

        master_keys = {
            "keys": {
                f"ed25519:{master.public_key}": master.public_key,
            },
            "user_id": user_id,
            "usage": ["master"],
        }

        master_keys["signatures"] = {}

        self_signing_keys = {
            "keys": {
                f"ed25519:{self_signing.public_key}": self_signing.public_key,
            },
            "user_id": user_id,
            "usage": ["self_signing"],
        }

        self_signature = master.sign(Api.to_canonical_json(self_signing_keys))

        self_signing_keys["signatures"] = {
            user_id: {
                f"ed25519:{master.public_key}": self_signature
            }
        }

        user_keys = {
            "keys": {
                f"ed25519:{user.public_key}": user.public_key,
            },
            "user_id": user_id,
            "usage": ["user_signing"],
        }

        user_signature = master.sign(Api.to_canonical_json(user_keys))

        user_keys["signatures"] = {
            user_id: {
                f"ed25519:{master.public_key}": user_signature
            }
        }

        master_keys = MasterPubkeys(
            user_id,
            master_keys["keys"],
            master_keys["signatures"],
            master_keys["usage"],
        )

        self_signing_keys = SelfSigningPubkeys(
            user_id,
            self_signing_keys["keys"],
            self_signing_keys["signatures"],
            self_signing_keys["usage"],
        )

        user_signing_keys = UserSigningPubkeys(
            user_id,
            user_keys["keys"],
            user_keys["signatures"],
            user_keys["usage"],
        )

        return UserIdentity(
            user_id,
            master_keys,
            user_signing_keys,
            self_signing_keys
        )

    def olm_key_pair(self, device_id):
        keys = OlmAccount().identity_keys
        return {f"{key_type}:{device_id}": key for key_type, key in keys.items()}

    def olm_device(self):
        user_id = faker.mx_id()
        device_id = faker.device_id()
        key_pair = faker.olm_key_pair(device_id)

        return OlmDevice(
            user_id,
            device_id,
            key_pair,
        )

    def ed25519_key(self):
        device_id = faker.device_id()
        return Ed25519Key(
            faker.mx_id(),
            device_id,
            faker.olm_key_pair(device_id)[f"ed25519:{device_id}"]
        )


faker.add_provider(Provider)


ephemeral_dir = os.path.join(os.curdir, "tests/data/encryption")


def ephemeral(func):
    def wrapper(*args, **kwargs):
        try:
            ret = func(*args, **kwargs)
        finally:
            os.remove(os.path.join(
                ephemeral_dir,
                "@ephemeral:example.org_DEVICEID.db"
            ))
        return ret
    return wrapper


class FrameFactory:
    """
    A class containing lots of helper methods and state to build frames. This
    allows test cases to easily build correct HTTP/2 frames to feed to
    hyper-h2.
    """
    def __init__(self):
        self.encoder = Encoder()

    def refresh_encoder(self):
        self.encoder = Encoder()

    def preamble(self):
        return b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n'

    def build_headers_frame(self,
                            headers,
                            flags=[],
                            stream_id=1,
                            **priority_kwargs):
        """
        Builds a single valid headers frame out of the contained headers.
        """
        f = HeadersFrame(stream_id)
        f.data = self.encoder.encode(headers)
        f.flags.add('END_HEADERS')
        for flag in flags:
            f.flags.add(flag)

        for k, v in priority_kwargs.items():
            setattr(f, k, v)

        return f

    def build_continuation_frame(self, header_block, flags=[], stream_id=1):
        """
        Builds a single continuation frame out of the binary header block.
        """
        f = ContinuationFrame(stream_id)
        f.data = header_block
        f.flags = set(flags)

        return f

    def build_data_frame(self, data, flags=None, stream_id=1, padding_len=0):
        """
        Builds a single data frame out of a chunk of data.
        """
        flags = set(flags) if flags is not None else set()
        f = DataFrame(stream_id)
        f.data = data
        f.flags = flags

        if padding_len:
            flags.add('PADDED')
            f.pad_length = padding_len

        return f

    def build_settings_frame(self, settings, ack=False):
        """
        Builds a single settings frame.
        """
        f = SettingsFrame(0)
        if ack:
            f.flags.add('ACK')

        f.settings = settings
        return f

    def build_window_update_frame(self, stream_id, increment):
        """
        Builds a single WindowUpdate frame.
        """
        f = WindowUpdateFrame(stream_id)
        f.window_increment = increment
        return f

    def build_ping_frame(self, ping_data, flags=None):
        """
        Builds a single Ping frame.
        """
        f = PingFrame(0)
        f.opaque_data = ping_data
        if flags:
            f.flags = set(flags)

        return f

    def build_goaway_frame(self,
                           last_stream_id,
                           error_code=0,
                           additional_data=b''):
        """
        Builds a single GOAWAY frame.
        """
        f = GoAwayFrame(0)
        f.error_code = error_code
        f.last_stream_id = last_stream_id
        f.additional_data = additional_data
        return f

    def build_rst_stream_frame(self, stream_id, error_code=0):
        """
        Builds a single RST_STREAM frame.
        """
        f = RstStreamFrame(stream_id)
        f.error_code = error_code
        return f

    def build_push_promise_frame(self,
                                 stream_id,
                                 promised_stream_id,
                                 headers,
                                 flags=[]):
        """
        Builds a single PUSH_PROMISE frame.
        """
        f = PushPromiseFrame(stream_id)
        f.promised_stream_id = promised_stream_id
        f.data = self.encoder.encode(headers)
        f.flags = set(flags)
        f.flags.add('END_HEADERS')
        return f

    def build_priority_frame(self,
                             stream_id,
                             weight,
                             depends_on=0,
                             exclusive=False):
        """
        Builds a single priority frame.
        """
        f = PriorityFrame(stream_id)
        f.depends_on = depends_on
        f.stream_weight = weight
        f.exclusive = exclusive
        return f

    def build_alt_svc_frame(self, stream_id, origin, field):
        """
        Builds a single ALTSVC frame.
        """
        f = AltSvcFrame(stream_id)
        f.origin = origin
        f.field = field
        return f
