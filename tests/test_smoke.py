def test_package_imports():
    import cb_photo_loader
    assert isinstance(cb_photo_loader.__version__, str)
