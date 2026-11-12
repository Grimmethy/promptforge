"""What the engine needs from one library forge. Subclass it per forge (adapters/characterforge.py, adapters/objectforge.py)."""


class Adapter:
    name = "forge"          # the forge name, e.g. 'characters'; it names the manifest and pid files

    def entries(self):
        """Entry dicts, each with a 'slug', already in priority order (the engine does not reorder them)."""
        raise NotImplementedError

    def roles(self):
        """The role ids to fill, in the order they are generated."""
        raise NotImplementedError

    def counts(self, slug):
        """{role: number of pictures linked}: every verdict counts, a rejected picture still holds its slot."""
        raise NotImplementedError

    def target(self, entry, role):
        """How many pictures this entry wants in this role (default 10, may be lower)."""
        return 10

    def build_spec(self, entry, role, rnd):
        """(tag, spec): the variation's tag and the PromptForge /api/generate body for round rnd."""
        raise NotImplementedError

    def link(self, slug, role, tag, rnd, spec, job, name, data):
        """Link the picture to the entry and return the linked record dict (it has 'file'). Never deletes or replaces anything."""
        raise NotImplementedError
