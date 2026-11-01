from django import forms
from django.core.exceptions import ValidationError

from .validators import validate_media_file


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """A file field that accepts several files at once."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/*,video/mp4"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data]
        return [single_clean(data, initial)]


class BulkMediaUploadForm(forms.Form):
    files = MultipleFileField(
        label="Photos and videos",
        help_text="Select several files at once. Photos: JPG, PNG, WebP, HEIC (max 10 MB each). Videos: MP4 (max 100 MB each).",
    )

    def clean_files(self):
        files = self.cleaned_data["files"]
        errors = []
        for f in files:
            try:
                validate_media_file(f)
            except ValidationError as e:
                errors.extend(e.messages)
        if errors:
            raise ValidationError(errors)
        return files
