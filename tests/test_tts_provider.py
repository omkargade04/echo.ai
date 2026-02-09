"""Tests for echo.tts.provider — TTSProvider abstract base class."""

import pytest

from echo.tts.provider import TTSProvider


# ---------------------------------------------------------------------------
# TestAbstractBaseClass — ABC instantiation and enforcement
# ---------------------------------------------------------------------------


class TestAbstractBaseClass:
    """Tests for TTSProvider ABC interface requirements."""

    def test_cannot_instantiate_directly(self):
        """TTSProvider is an ABC and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            TTSProvider()

    def test_concrete_subclass_implementing_all_methods(self):
        """A concrete class implementing all abstract methods can be instantiated."""

        class ConcreteTTS(TTSProvider):
            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

            @property
            def is_available(self) -> bool:
                return True

            async def synthesize(self, text: str) -> bytes | None:
                return b"audio"

            @property
            def provider_name(self) -> str:
                return "concrete"

        instance = ConcreteTTS()
        assert isinstance(instance, TTSProvider)

    def test_concrete_subclass_is_recognized_as_tts_provider(self):
        """isinstance check should return True for concrete implementations."""

        class AnotherConcrete(TTSProvider):
            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

            @property
            def is_available(self) -> bool:
                return False

            async def synthesize(self, text: str) -> bytes | None:
                return None

            @property
            def provider_name(self) -> str:
                return "another"

        instance = AnotherConcrete()
        assert isinstance(instance, TTSProvider)

    def test_missing_start_prevents_instantiation(self):
        """Missing start() method prevents instantiation."""

        with pytest.raises(TypeError):

            class MissingStart(TTSProvider):
                async def stop(self) -> None:
                    pass

                @property
                def is_available(self) -> bool:
                    return True

                async def synthesize(self, text: str) -> bytes | None:
                    return None

                @property
                def provider_name(self) -> str:
                    return "missing"

            MissingStart()

    def test_missing_stop_prevents_instantiation(self):
        """Missing stop() method prevents instantiation."""

        with pytest.raises(TypeError):

            class MissingStop(TTSProvider):
                async def start(self) -> None:
                    pass

                @property
                def is_available(self) -> bool:
                    return True

                async def synthesize(self, text: str) -> bytes | None:
                    return None

                @property
                def provider_name(self) -> str:
                    return "missing"

            MissingStop()

    def test_missing_is_available_prevents_instantiation(self):
        """Missing is_available property prevents instantiation."""

        with pytest.raises(TypeError):

            class MissingAvailable(TTSProvider):
                async def start(self) -> None:
                    pass

                async def stop(self) -> None:
                    pass

                async def synthesize(self, text: str) -> bytes | None:
                    return None

                @property
                def provider_name(self) -> str:
                    return "missing"

            MissingAvailable()

    def test_missing_synthesize_prevents_instantiation(self):
        """Missing synthesize() method prevents instantiation."""

        with pytest.raises(TypeError):

            class MissingSynthesize(TTSProvider):
                async def start(self) -> None:
                    pass

                async def stop(self) -> None:
                    pass

                @property
                def is_available(self) -> bool:
                    return True

                @property
                def provider_name(self) -> str:
                    return "missing"

            MissingSynthesize()

    def test_missing_provider_name_prevents_instantiation(self):
        """Missing provider_name property prevents instantiation."""

        with pytest.raises(TypeError):

            class MissingProviderName(TTSProvider):
                async def start(self) -> None:
                    pass

                async def stop(self) -> None:
                    pass

                @property
                def is_available(self) -> bool:
                    return True

                async def synthesize(self, text: str) -> bytes | None:
                    return None

            MissingProviderName()
